"""The follow-up question about a closed journal entry (FR-CAP-6).

When an entry closes, the `extract_entry` job (`app/jobs.py`) decides once whether to ask one
question before it asks which role the entry is about (`app/telegram/roles.py`). It asks when the
light-tier check (`app/follow_up.py`) finds the numbers, impact or the owner's part missing, but
only while the owner is likely still in the chat: within the entry timeout of the entry closing,
and with no other entry open or waiting for an answer, and no catch-up interview under way
(`app/telegram/catch_up.py`), so that the answer can't be taken for anything else. An answer in
the interview gets no follow-up question: the interview asks its own. The question replies to the
entry's first message, with a Skip button.

- The owner's next message, unless it's a command, is the answer if it comes within the entry
  timeout of the question. It joins the entry, after its other messages, so extraction reads it.
- Skip, or `/skip`, lets the entry go on without an answer.
- A question that gets neither within the timeout lapses: a later message is a new note.

Each lets the entry's jobs go on to its role. The question is written from the entry's text, so
it's never logged.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CatchUp, JournalEntry
from app.follow_up import follow_up_question
from app.llm.client import LLMClient, LLMInvalidOutput, LLMRefusal
from app.telegram.api import BotCall, send
from app.telegram.processing import chat_of, entry_text, reply_to_entry
from app.telegram.roles import Answer, has_role
from app.telegram.updates import CallbackQuery

logger = logging.getLogger(__name__)

SKIP = "+skip"
"""The Skip button's data. Role buttons carry a role's ID, which never starts with "+"."""
SKIP_KEYBOARD = {"inline_keyboard": [[{"text": "Skip", "callback_data": SKIP}]]}
SKIPPED = "Skipped the question."
NOTHING_TO_SKIP = "There's no question to skip."
NOT_WAITING = "This question doesn't wait for an answer any more."


@dataclass(frozen=True)
class FollowUp:
    """The follow-up question to send about a closed entry."""

    chat_id: int
    text: str
    options: dict[str, Any]
    """`sendMessage`'s other parameters: the Skip button, and the message the question replies
    to."""


async def follow_up(
    session: AsyncSession,
    entry_id: uuid.UUID,
    client: LLMClient,
    *,
    now: datetime,
    timeout: timedelta,
) -> FollowUp | None:
    """The question to ask about the closed entry as of `now`, if one is worth asking now. Marks
    the entry as waiting for the answer. The bot decides once per entry; until the owner answers
    or skips, or the question lapses here after `timeout`, `role_question` doesn't ask about the
    entry. A refusal or an unusable reply from the model means no question, not a failed entry."""
    entry = await session.scalar(
        select(JournalEntry).where(JournalEntry.id == entry_id).with_for_update()
    )
    if entry is None or entry.closed_at is None:
        return None
    if entry.processed_at is not None or entry.failed_at is not None:
        return None
    if entry.awaiting_follow_up:
        if lapsed(entry, now, timeout):
            entry.awaiting_follow_up = False
        return None
    if entry.follow_up_at is not None or entry.role_asked_at is not None or has_role(entry):
        return None
    if entry.question is not None:
        return None
    entry.follow_up_at = now
    if now - entry.closed_at >= timeout or await _busy(session, entry, now, timeout):
        return None
    chat_id = await chat_of(session, entry.user_id)
    note = await entry_text(session, entry_id)
    if chat_id is None or not note:
        return None
    try:
        question = await asyncio.to_thread(follow_up_question, note, client)
    except (LLMRefusal, LLMInvalidOutput) as error:
        reason = type(error).__name__
        logger.info("no follow-up question for journal entry %s: %s", entry_id, reason)
        return None
    if question is None:
        return None
    entry.awaiting_follow_up = True
    options = {"reply_markup": SKIP_KEYBOARD, **await reply_to_entry(session, entry_id, chat_id)}
    return FollowUp(chat_id, question, options)


async def record_follow_up(
    session: AsyncSession, entry_id: uuid.UUID, message_id: int | None
) -> None:
    """Keep the ID of the message that asked, so that its Skip button answers for the entry. When
    the bot couldn't send it, the entry doesn't wait for an answer."""
    values: dict[str, Any] = (
        {"follow_up_question_id": message_id}
        if message_id is not None
        else {"awaiting_follow_up": False}
    )
    await session.execute(update(JournalEntry).where(JournalEntry.id == entry_id).values(values))


def lapsed(entry: JournalEntry, at: datetime, timeout: timedelta) -> bool:
    """Whether the entry's question, asked `timeout` or more before `at`, has lapsed."""
    return entry.follow_up_at is None or at - entry.follow_up_at >= timeout


async def awaiting(session: AsyncSession, owner: uuid.UUID) -> JournalEntry | None:
    """The entry whose follow-up question waits for the owner, if one does. It may have lapsed."""
    return await session.scalar(
        select(JournalEntry)
        .where(
            JournalEntry.user_id == owner,
            JournalEntry.awaiting_follow_up,
            JournalEntry.processed_at.is_(None),
            JournalEntry.failed_at.is_(None),
        )
        .with_for_update()
    )


def answered(entry: JournalEntry, chat_id: int) -> Answer:
    """Take the owner's message as the answer, which the caller adds to the entry. The question
    stops waiting and loses its Skip button."""
    entry.awaiting_follow_up = False
    return Answer(_without_skip(chat_id, entry), ready=entry.id)


async def skip(session: AsyncSession, owner: uuid.UUID, chat_id: int) -> Answer:
    """Answer `/skip`: the waiting question, lapsed or not, stops waiting."""
    entry = await awaiting(session, owner)
    if entry is None:
        return Answer((send(chat_id, NOTHING_TO_SKIP),))
    entry.awaiting_follow_up = False
    return Answer((send(chat_id, SKIPPED), *_without_skip(chat_id, entry)), ready=entry.id)


async def stop_waiting(session: AsyncSession, owner: uuid.UUID, chat_id: int) -> Answer:
    """Stop the question that waits for an answer, lapsed or not, without a word, when the
    catch-up interview starts (`app/telegram/catch_up.py`), so that the owner's next message
    answers the interview. The question loses its Skip button, and the entry goes on to its role.
    """
    entry = await awaiting(session, owner)
    if entry is None:
        return Answer(())
    entry.awaiting_follow_up = False
    return Answer(_without_skip(chat_id, entry), ready=entry.id)


async def press_skip(session: AsyncSession, owner: uuid.UUID, query: CallbackQuery) -> Answer:
    """Answer a press of the question's Skip button, as `/skip` does."""
    message = query.message
    entry = None
    if message is not None:
        entry = await session.scalar(
            select(JournalEntry)
            .where(
                JournalEntry.user_id == owner,
                JournalEntry.follow_up_question_id == message.message_id,
                JournalEntry.awaiting_follow_up,
                JournalEntry.processed_at.is_(None),
                JournalEntry.failed_at.is_(None),
            )
            .with_for_update()
        )
    if message is None or entry is None:
        return Answer((_answer(query, NOT_WAITING),))
    entry.awaiting_follow_up = False
    return Answer((_answer(query), *_without_skip(message.chat.id, entry)), ready=entry.id)


async def _busy(
    session: AsyncSession, entry: JournalEntry, now: datetime, timeout: timedelta
) -> bool:
    """Whether the owner has another entry open, or one that waits for their next message: a new
    role's name or a follow-up answer, or a catch-up interview under way. Another entry's lapsed
    question stops waiting here."""
    others = (JournalEntry.user_id == entry.user_id, JournalEntry.id != entry.id)
    await session.execute(
        update(JournalEntry)
        .where(*others, JournalEntry.awaiting_follow_up, JournalEntry.follow_up_at <= now - timeout)
        .values(awaiting_follow_up=False)
    )
    waiting = or_(
        JournalEntry.closed_at.is_(None),
        and_(
            or_(JournalEntry.naming_role, JournalEntry.awaiting_follow_up),
            JournalEntry.processed_at.is_(None),
            JournalEntry.failed_at.is_(None),
        ),
    )
    interviewing = exists().where(
        CatchUp.user_id == entry.user_id, CatchUp.status.in_(("thinking", "asking"))
    )
    entries = exists().where(*others, waiting)
    return bool(await session.scalar(select(or_(entries, interviewing))))


def _answer(query: CallbackQuery, text: str | None = None) -> BotCall:
    """Tell Telegram the press was handled, with a short notice if there's something to say."""
    params: dict[str, Any] = {"callback_query_id": query.id}
    if text is not None:
        params["text"] = text
    return BotCall("answerCallbackQuery", params)


def _without_skip(chat_id: int, entry: JournalEntry) -> tuple[BotCall, ...]:
    """Remove the question's Skip button, once it has nothing left to skip."""
    if entry.follow_up_question_id is None:
        return ()
    params = {"chat_id": chat_id, "message_id": entry.follow_up_question_id}
    return (BotCall("editMessageReplyMarkup", params),)
