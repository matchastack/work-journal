"""The owner's reminders in Telegram (FR-REM-1, FR-REM-2, FR-REM-3).

The `send_reminders` job (`app/jobs.py`) runs every minute under `wj worker` and on every tick.
For each linked owner, `reminder_due` decides with `app/reminders.py` whether their reminder is
due: it returns the reminder to send, or records a skipped one, and `record_sent` records one
sent. The reminder's text is fixed, so no model writes it. Its buttons (FR-REM-3):

- "Nothing this week" is logged in the reminder record, as the Monday of the reminder's week,
  and skips that week's later reminders. A press after the weekend means the week just ended.
- "Remind me tomorrow" brings the reminder back the next day at the schedule's time. While
  reminders are paused, it only says so, since the pause would skip that reminder.

Only the newest reminder's buttons work; a press on an older one changes nothing. `/pause` and
`/resume` set the pause in the owner's reminder setting (FR-SET-2), and the Settings page changes
the whole setting (`change_reminders`).

The owner's `telegram_links` row, locked, serializes every change to the reminder record and
setting: the job, the buttons, `/pause`, `/resume` and the Settings page. So two runs can't both
send one reminder, and a press can't undo what a run records. Only user IDs, times and outcomes
are logged.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from datetime import time as clock_time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JournalMessage, TelegramLink
from app.db.settings import (
    get_reminder_settings,
    get_reminder_state,
    save_reminder_settings,
    save_reminder_state,
)
from app.reminders import Schedule, describe, due, effective, recorded, tomorrow, week_of
from app.schema.settings import ReminderSettings
from app.telegram.api import BotCall, send
from app.telegram.roles import Answer
from app.telegram.updates import CallbackQuery

logger = logging.getLogger(__name__)

NOTHING_THIS_WEEK = "+nothing"
"""The "Nothing this week" button's data. Role buttons carry a role's ID, which never starts with
"+"."""
REMIND_TOMORROW = "+tomorrow"
"""The "Remind me tomorrow" button's data."""
BUTTONS = (NOTHING_THIS_WEEK, REMIND_TOMORROW)
KEYBOARD = {
    "inline_keyboard": [
        [{"text": "Nothing this week", "callback_data": NOTHING_THIS_WEEK}],
        [{"text": "Remind me tomorrow", "callback_data": REMIND_TOMORROW}],
    ]
}
REMINDER = (
    "A gentle nudge: anything from work lately worth keeping? A few lines are enough, with "
    "numbers if you have them."
)
NOTED = "Noted: nothing this week. I'll check in next week."
PASSED = "This reminder has passed."
PAUSED = "Paused your reminders. Send /resume to restart them."
ALREADY_PAUSED = "Your reminders are already paused. Send /resume to restart them."
PAUSED_SNOOZE = "Your reminders are paused. Send /resume to restart them."


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
    text = REMINDER  # T-036 adds an open question here (FR-REM-5).
    return Reminder(chat_id, found.slot, text, {"reply_markup": KEYBOARD})


async def record_sent(
    session: AsyncSession, owner: uuid.UUID, reminder: Reminder, message_id: int | None
) -> None:
    """Record the reminder as sent, as `message_id`, whose buttons now work instead of any older
    reminder's. None means the bot couldn't send it: it's dealt with all the same."""
    state = await get_reminder_state(session, owner)
    changed = recorded(state, reminder.slot, message_id=message_id, sent=True)
    await save_reminder_state(session, owner, changed)


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
    reminders are paused."""
    message = query.message
    await _lock(session, owner)
    state = await get_reminder_state(session, owner)
    if message is None or state.message_id is None or message.message_id != state.message_id:
        return Answer((_answer(query, PASSED),))
    schedule = effective(await get_reminder_settings(session, owner), defaults)
    if query.data == REMIND_TOMORROW and schedule.paused:
        return Answer((_answer(query, PAUSED_SNOOZE),))
    if query.data == NOTHING_THIS_WEEK:
        week = week_of(state.message_slot or now, schedule.zone)
        weeks = tuple(sorted({*state.nothing_weeks, week}))
        changes: dict[str, object] = {"nothing_weeks": weeks, "snoozed_until": None}
        text = NOTED
    else:
        changes = {"snoozed_until": tomorrow(schedule, now)}
        text = snoozed_text(schedule.time)
    changed = state.model_copy(update={**changes, "message_id": None, "message_slot": None})
    await save_reminder_state(session, owner, changed)
    chat_id = message.chat.id
    calls = (_answer(query), _without_buttons(chat_id, message.message_id), send(chat_id, text))
    return Answer(calls)


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


async def change_reminders(
    session: AsyncSession, owner: uuid.UUID, reminders: ReminderSettings
) -> None:
    """Replace the owner's reminder schedule and pause, from the Settings page (FR-SET-2),
    holding the owner's lock, so that it can't interleave with `/pause`, `/resume` or a run of
    the job. The next reminder due on the new schedule goes out as usual."""
    await _lock(session, owner)
    await save_reminder_settings(session, owner, reminders)


async def _lock(session: AsyncSession, owner: uuid.UUID) -> int | None:
    """Lock the owner's Telegram link until the transaction ends, and return its chat. None when
    the owner has no linked chat."""
    return await session.scalar(
        select(TelegramLink.chat_id).where(TelegramLink.user_id == owner).with_for_update()
    )


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
