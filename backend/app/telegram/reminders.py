"""The owner's reminders in Telegram (FR-REM-1, FR-REM-2, FR-REM-3, FR-REM-5).

The `send_reminders` job (`app/jobs.py`) runs every minute under `wj worker` and on every tick.
For each linked owner, `reminder_due` decides with `app/reminders.py` whether their reminder is
due: it returns the reminder to send, or records a skipped one, and `record_sent` records one
sent. The reminder's text is fixed, so no model writes it. It may end with one open question
from the owner's latest profile version, as the profile words it: the most valuable one the
journal holds no answer to and this round hasn't asked (`app/open_questions.py`). The journal
holds an answer while an answer entry waits to be processed, or once one gave a fact. Its
buttons (FR-REM-3):

- "Answer the question", on a reminder that asks one, asks for the answer in one message. The
  owner's next message, unless it's a command, is the answer if it comes within the entry
  timeout (`take_answer`): an entry of its own, closed at once and linked to the question
  (FR-EXT-4), which isn't joined to an open entry. Its jobs run at once, and read the answer
  alone, as any note. Later, the wait has lapsed, and a message is an ordinary note.
- "Nothing this week" is logged in the reminder record, as the Monday of the reminder's week,
  and skips that week's later reminders. A press after the weekend means the week just ended.
- "Remind me tomorrow" brings the reminder back the next day at the schedule's time. While
  reminders are paused, it only says so, since the pause would skip that reminder.

Only the newest reminder's buttons work; a press on an older one changes nothing. `/pause` and
`/resume` set the pause in the owner's reminder setting (FR-SET-2).

The owner's `telegram_links` row, locked, serializes every change to the reminder record and
setting: the job, the buttons, the answer, `/pause` and `/resume`. So two runs can't both send
one reminder, and a press can't undo what a run records. When a journal entry is locked too, as
after a lapsed wait for an answer, this lock comes first. Only user IDs, times and outcomes are
logged: never a question or its ID, which can name an employer.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time as clock_time
from typing import Any

from sqlalchemy import String, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FactRow, JournalEntry, JournalMessage, TelegramLink
from app.db.profiles import latest_version
from app.db.settings import (
    get_reminder_settings,
    get_reminder_state,
    save_reminder_settings,
    save_reminder_state,
)
from app.open_questions import NextQuestion, answer_role, awaits_answer, next_question
from app.reminders import Schedule, describe, due, effective, recorded, tomorrow, week_of
from app.schema.settings import Answering, ReminderState
from app.telegram.api import BotCall, send
from app.telegram.roles import Answer
from app.telegram.updates import CallbackQuery

logger = logging.getLogger(__name__)

NOTHING_THIS_WEEK = "+nothing"
"""The "Nothing this week" button's data. Role buttons carry a role's ID, which never starts with
"+"."""
REMIND_TOMORROW = "+tomorrow"
"""The "Remind me tomorrow" button's data."""
ANSWER_QUESTION = "+answer"
"""The "Answer the question" button's data, on a reminder that asks an open question."""
BUTTONS = (NOTHING_THIS_WEEK, REMIND_TOMORROW, ANSWER_QUESTION)
KEYBOARD = {
    "inline_keyboard": [
        [{"text": "Nothing this week", "callback_data": NOTHING_THIS_WEEK}],
        [{"text": "Remind me tomorrow", "callback_data": REMIND_TOMORROW}],
    ]
}
QUESTION_KEYBOARD = {
    "inline_keyboard": [
        [{"text": "Answer the question", "callback_data": ANSWER_QUESTION}],
        *KEYBOARD["inline_keyboard"],
    ]
}
"""The buttons of a reminder that asks an open question (FR-REM-5)."""
REMINDER = (
    "A gentle nudge: anything from work lately worth keeping? A few lines are enough, with "
    "numbers if you have them."
)
QUESTION_INTRO = "And one question from your master resume, if you know the answer:"
"""What comes before the open question a reminder asks (FR-REM-5)."""
ANSWER_PROMPT = (
    "Send your answer in one message that makes sense on its own, as you'd write a note, with "
    "numbers if you have them."
)
"""The answer is read alone, without the question (FR-EXT-4), so it has to say what it's about."""
ANSWER_REPLY = {"force_reply": True, "input_field_placeholder": "Your answer"}
NOTED = "Noted: nothing this week. I'll check in next week."
PASSED = "This reminder has passed."
PAUSED = "Paused your reminders. Send /resume to restart them."
ALREADY_PAUSED = "Your reminders are already paused. Send /resume to restart them."
PAUSED_SNOOZE = "Your reminders are paused. Send /resume to restart them."


def reminder_text(question: str | None = None) -> str:
    """The reminder, with the open question it asks, if it asks one (FR-REM-5)."""
    return REMINDER if question is None else f"{REMINDER}\n\n{QUESTION_INTRO}\n{question}"


def snoozed_text(at: clock_time) -> str:
    """The answer to "Remind me tomorrow", which brings the reminder back at `at`, local time."""
    return f"OK, I'll remind you tomorrow at {at:%H:%M}."


def resumed_text(schedule: Schedule) -> str:
    """The answer to `/resume`. It says when reminders go out, since no page shows it yet."""
    return f"Reminders are back on: {describe(schedule)}."


def already_on_text(schedule: Schedule) -> str:
    return f"Reminders are already on: {describe(schedule)}."


@dataclass(frozen=True)
class Reminder:
    """A reminder to send."""

    chat_id: int
    slot: datetime
    """The time it's for, in UTC."""
    text: str
    options: dict[str, Any]
    """`sendMessage`'s other parameters: the buttons."""
    question_id: str | None = None
    """The open question it asks, if it asks one."""
    asked: tuple[str, ...] = ()
    """The questions asked in the round once it's sent, this one last."""


async def reminder_due(
    session: AsyncSession, owner: uuid.UUID, *, now: datetime, defaults: Schedule
) -> Reminder | None:
    """The owner's reminder, if one is to go out at `now`, on their schedule or else `defaults`.
    A reminder skipped at `now` is recorded as dealt with instead. Keep the transaction open
    until the reminder is sent and `record_sent` records it: it holds the owner's lock."""
    chat_id = await _lock(session, owner)
    if chat_id is None:
        return None
    schedule = effective(await get_reminder_settings(session, owner), defaults)
    state = await get_reminder_state(session, owner)
    found = due(schedule, state, now=now, last_journaled=await _last_journaled(session, owner))
    if found is None:
        return None
    if found.outcome != "send":
        await save_reminder_state(session, owner, recorded(state, found.slot))
        logger.info(
            "skipped the reminder for user %s at %s: %s",
            owner,
            found.slot.isoformat(),
            found.outcome,
        )
        return None
    asking = await _next_question(session, owner, state)
    if asking is None:
        return Reminder(chat_id, found.slot, reminder_text(), {"reply_markup": KEYBOARD})
    question = asking.question
    text, options = reminder_text(question.question), {"reply_markup": QUESTION_KEYBOARD}
    return Reminder(chat_id, found.slot, text, options, question.id, asking.asked)


async def record_sent(
    session: AsyncSession, owner: uuid.UUID, reminder: Reminder, message_id: int | None
) -> None:
    """Record the reminder as sent, as `message_id`, whose buttons now work instead of any older
    reminder's, and the question it asks as asked. None means the bot couldn't send it: it's
    dealt with all the same, but its question wasn't asked."""
    state = await get_reminder_state(session, owner)
    changed = recorded(state, reminder.slot, message_id=message_id, sent=True)
    question_id = None if message_id is None else reminder.question_id
    changes: dict[str, object] = {"message_question": question_id}
    if question_id is not None:
        changes["asked"] = reminder.asked
    await save_reminder_state(session, owner, changed.model_copy(update=changes))


async def press_reminder(
    session: AsyncSession,
    owner: uuid.UUID,
    query: CallbackQuery,
    *,
    now: datetime,
    defaults: Schedule,
) -> Answer:
    """Answer a press, at `now`, of one of the newest reminder's buttons, which then go away. A
    press on an older reminder only gets a notice, and so does "Remind me tomorrow" while
    reminders are paused. "Answer the question" works while they're paused: the owner's next
    message is then the answer."""
    message = query.message
    await _lock(session, owner)
    state = await get_reminder_state(session, owner)
    if message is None or state.message_id is None or message.message_id != state.message_id:
        return Answer((_answer(query, PASSED),))
    schedule = effective(await get_reminder_settings(session, owner), defaults)
    if query.data == REMIND_TOMORROW and schedule.paused:
        return Answer((_answer(query, PAUSED_SNOOZE),))
    chat_id = message.chat.id
    changes: dict[str, object]
    if query.data == ANSWER_QUESTION:
        if state.message_question is None:
            return Answer((_answer(query, PASSED),))
        changes = {"answering": Answering(question_id=state.message_question, since=now)}
        reply = send(chat_id, ANSWER_PROMPT, reply_markup=ANSWER_REPLY)
    elif query.data == NOTHING_THIS_WEEK:
        week = week_of(state.message_slot or now, schedule.zone)
        weeks = tuple(sorted({*state.nothing_weeks, week}))
        changes = {"nothing_weeks": weeks, "snoozed_until": None}
        reply = send(chat_id, NOTED)
    else:
        changes = {"snoozed_until": tomorrow(schedule, now)}
        reply = send(chat_id, snoozed_text(schedule.time))
    pressed = {"message_id": None, "message_slot": None, "message_question": None}
    await save_reminder_state(session, owner, state.model_copy(update={**changes, **pressed}))
    return Answer((_answer(query), _without_buttons(chat_id, message.message_id), reply))


async def take_answer(
    session: AsyncSession, owner: uuid.UUID, sent_at: datetime, timeout: timedelta
) -> uuid.UUID | None:
    """The entry for the owner's message sent at `sent_at`, when it answers the open question
    whose "Answer the question" they pressed less than `timeout` before (FR-EXT-4). It's an entry
    of its own, closed at once and linked to the question, with the role that holds the bullets
    needing the question, when one role holds them all; otherwise the bot asks which role. The
    caller journals the message in it. The wait ends either way: None means the message isn't an
    answer."""
    if (await get_reminder_state(session, owner)).answering is None:
        return None
    await _lock(session, owner)
    state = await get_reminder_state(session, owner)
    answering = state.answering
    if answering is None:
        return None
    await save_reminder_state(session, owner, state.model_copy(update={"answering": None}))
    if not awaits_answer(answering, sent_at, timeout):
        return None
    version = await latest_version(session, owner)
    role_id = None if version is None else answer_role(version.profile, answering.question_id)
    entry = JournalEntry(
        user_id=owner,
        opened_at=sent_at,
        last_message_at=sent_at,
        closed_at=sent_at,
        closed_by="done",
        role_id=role_id,
        open_question_id=answering.question_id,
    )
    session.add(entry)
    await session.flush()
    return entry.id


async def pause(session: AsyncSession, owner: uuid.UUID, chat_id: int) -> Answer:
    """Answer `/pause`: no reminders until `/resume`. The schedule stays as it is."""
    await _lock(session, owner)
    stored = await get_reminder_settings(session, owner)
    if stored.paused:
        return Answer((send(chat_id, ALREADY_PAUSED),))
    await save_reminder_settings(session, owner, stored.model_copy(update={"paused": True}))
    return Answer((send(chat_id, PAUSED),))


async def resume(
    session: AsyncSession, owner: uuid.UUID, chat_id: int, *, defaults: Schedule
) -> Answer:
    """Answer `/resume`: reminders go out again from the next one due. One the job skipped while
    they were paused isn't sent late."""
    await _lock(session, owner)
    stored = await get_reminder_settings(session, owner)
    schedule = effective(stored, defaults)
    if not stored.paused:
        return Answer((send(chat_id, already_on_text(schedule)),))
    await save_reminder_settings(session, owner, stored.model_copy(update={"paused": False}))
    return Answer((send(chat_id, resumed_text(schedule)),))


async def _lock(session: AsyncSession, owner: uuid.UUID) -> int | None:
    """Lock the owner's Telegram link until the transaction ends, and return its chat. None when
    the owner has no linked chat."""
    return await session.scalar(
        select(TelegramLink.chat_id).where(TelegramLink.user_id == owner).with_for_update()
    )


async def _next_question(
    session: AsyncSession, owner: uuid.UUID, state: ReminderState
) -> NextQuestion | None:
    """The open question the owner's next reminder asks, if any (FR-REM-5)."""
    version = await latest_version(session, owner)
    if version is None:
        return None
    answered = await _answered(session, owner)
    return next_question(version.profile, answered=answered, asked=state.asked)


async def _answered(session: AsyncSession, owner: uuid.UUID) -> set[str]:
    """The open questions the owner's journal holds an answer to: an answer entry (FR-EXT-4)
    that hasn't failed, and either waits to be processed or gave a fact. One that gave none, such
    as "I don't know", leaves its question open to be asked again."""
    gave_facts = exists().where(
        FactRow.user_id == owner, FactRow.entry_id == cast(JournalEntry.id, String)
    )
    found = await session.scalars(
        select(JournalEntry.open_question_id).where(
            JournalEntry.user_id == owner,
            JournalEntry.open_question_id.is_not(None),
            JournalEntry.failed_at.is_(None),
            or_(JournalEntry.processed_at.is_(None), gave_facts),
        )
    )
    return {question_id for question_id in found if question_id is not None}


async def _last_journaled(session: AsyncSession, owner: uuid.UUID) -> datetime | None:
    """When the owner last sent the journal a message (FR-REM-2)."""
    return await session.scalar(
        select(func.max(JournalMessage.sent_at)).where(
            JournalMessage.user_id == owner, JournalMessage.sender == "owner"
        )
    )


def _answer(query: CallbackQuery, text: str | None = None) -> BotCall:
    """Tell Telegram the press was handled, with a short notice if there's something to say."""
    params: dict[str, Any] = {"callback_query_id": query.id}
    if text is not None:
        params["text"] = text
    return BotCall("answerCallbackQuery", params)


def _without_buttons(chat_id: int, message_id: int) -> BotCall:
    """Remove a reminder's buttons, once one is pressed."""
    return BotCall("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id})
