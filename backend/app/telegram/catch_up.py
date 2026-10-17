"""The catch-up interview in the chat (FR-REM-4, FR-CAP-7).

It starts once, when the owner first links their chat, and again on `/catchup`. The bot asks
about the time since the profile was last updated, one question at a time (`app/catch_up.py`),
each with Skip and Stop for now buttons. The `catch_up_question` job (`app/jobs.py`) writes and
sends each question, so a question's message ID is always kept.

- The owner's next message, unless it's a command, answers the question, if it's sent after the
  question and within the entry timeout. It becomes a journal entry of its own, closed at once
  and kept with its question, so the entry's jobs ask which role it's about (FR-CAP-11) and save
  its facts. The next question follows. A message sent before the question, while the bot was
  writing it, is an ordinary note.
- Skip, or `/skip`, moves on to the next question.
- Stop for now, or `/done`, pauses the interview, and so does a question that lapses: a message
  after the timeout is an ordinary note, and the bot says so. `/catchup` carries on, asking the
  waiting question again.

After the last question, the bot offers `/refresh` to turn the answers into proposed changes.
Starting or resuming the interview stops a follow-up question that waits for an answer, and while
it runs, the bot asks no follow-up questions about other entries (`app/telegram/follow_up.py`),
so that the owner's next message is never ambiguous. Questions and answers are never logged.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catch_up import MAX_QUESTIONS, Turn, next_question
from app.db.models import CatchUp, JournalEntry
from app.db.profiles import latest_version
from app.llm.client import LLMClient
from app.telegram.api import BotCall, send
from app.telegram.follow_up import stop_waiting
from app.telegram.processing import chat_of
from app.telegram.updates import CallbackQuery

SKIP_QUESTION = "+catchup-skip"
STOP = "+catchup-stop"
"""The buttons' data. Role buttons carry a role's ID, which never starts with "+"."""
KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "Skip", "callback_data": SKIP_QUESTION},
            {"text": "Stop for now", "callback_data": STOP},
        ]
    ]
}
INTRO = (
    "Let's catch up on your work since your resume was last updated. I'll ask up to "
    f"{MAX_QUESTIONS} questions, one at a time. Answer each in one message that makes sense on "
    "its own, as you'd write a note, or press Skip. Press Stop for now, or send /done, to pause, "
    "and /catchup to carry on."
)
RESUMED = "Let's carry on with the catch-up."
THINKING = "I'm thinking of the next catch-up question."
SKIPPED = "Skipped the question."
PAUSED = "Paused the catch-up. Send /catchup to carry on where we left off."
FINISHED = (
    "That's all my questions, thanks. Once each answer has its role, send /refresh to turn your "
    "answers into proposed changes to your profile."
)
NO_ANSWERS = (
    "That's the end of the catch-up. Send me notes about your work whenever you like, or "
    "/catchup to start it again."
)
NOT_WAITING = "This question doesn't wait for an answer any more."

_turns = TypeAdapter(list[Turn])


@dataclass(frozen=True)
class CatchUpAnswer:
    """The bot's answer about the interview, the entry whose jobs can run now, such as an
    answer's, and the interview whose next question can be asked now."""

    calls: tuple[BotCall, ...]
    ready: uuid.UUID | None = None
    catch_up: uuid.UUID | None = None


@dataclass(frozen=True)
class Question:
    """A message for the job to send: the next question, or the interview's end."""

    chat_id: int
    text: str
    options: dict[str, Any]
    """`sendMessage`'s other parameters: a question's buttons."""
    waits: bool
    """Whether it's a question, whose message ID is to be kept."""


def turns_of(interview: CatchUp) -> list[Turn]:
    return _turns.validate_json(interview.transcript)


async def unfinished(session: AsyncSession, owner: uuid.UUID) -> CatchUp | None:
    """The owner's interview that isn't finished, if there is one."""
    return await session.scalar(
        select(CatchUp)
        .where(CatchUp.user_id == owner, CatchUp.status != "finished")
        .with_for_update()
    )


async def asking(session: AsyncSession, owner: uuid.UUID) -> CatchUp | None:
    """The owner's interview whose question waits for an answer, if one does. It may have
    lapsed."""
    interview = await unfinished(session, owner)
    return interview if interview is not None and interview.status == "asking" else None


def running(interview: CatchUp | None) -> bool:
    """Whether the interview is under way: writing its next question or waiting for an answer."""
    return interview is not None and interview.status in ("thinking", "asking")


def lapsed(interview: CatchUp, at: datetime, timeout: timedelta) -> bool:
    """Whether the interview's question, asked `timeout` or more before `at`, has lapsed."""
    return interview.asked_at is None or at - interview.asked_at >= timeout


def before_question(interview: CatchUp, at: datetime) -> bool:
    """Whether a message sent at `at` was sent before the question was asked, while the bot was
    writing it. Telegram gives whole seconds, so the comparison is to the second."""
    return interview.asked_at is not None and at < interview.asked_at.replace(microsecond=0)


def answers(interview: CatchUp, at: datetime, timeout: timedelta) -> bool:
    """Whether a message sent at `at` answers the interview's question."""
    return not before_question(interview, at) and not lapsed(interview, at, timeout)


def lapse(
    interview: CatchUp, chat_id: int, at: datetime, timeout: timedelta
) -> tuple[BotCall, ...]:
    """Handle a message sent at `at` that doesn't answer the question, and is an ordinary note
    instead. Unless it was sent before the question, the question has lapsed: the interview
    pauses, and the bot says so. Returns the calls that tell the owner."""
    if before_question(interview, at):
        return ()
    calls = pause(interview, chat_id)
    return (send(chat_id, lapsed_text(timeout)), *calls)


def lapsed_text(timeout: timedelta) -> str:
    minutes = round(timeout.total_seconds() / 60)
    return (
        f"That catch-up question waited more than {minutes} minutes, so I kept your message as an "
        "ordinary note and paused the catch-up. Send /catchup to carry on."
    )


async def start(session: AsyncSession, owner: uuid.UUID, chat_id: int) -> CatchUpAnswer:
    """Answer `/catchup`: start an interview, or carry on with the one under way or paused. A
    follow-up question that waits for an answer stops, and its entry goes on to its role."""
    stopped = await stop_waiting(session, owner, chat_id)
    interview = await unfinished(session, owner)
    if interview is None:
        interview = CatchUp(user_id=owner, transcript="[]")
        session.add(interview)
        await session.flush()
        calls = (send(chat_id, INTRO), *stopped.calls)
    elif interview.status == "thinking":
        calls = (send(chat_id, THINKING), *stopped.calls)
    else:
        calls = (send(chat_id, RESUMED), *_without_buttons(chat_id, interview), *stopped.calls)
        interview.status, interview.question_id = "thinking", None
    return CatchUpAnswer(calls, ready=stopped.ready, catch_up=interview.id)


async def first_run(session: AsyncSession, owner: uuid.UUID, chat_id: int) -> CatchUpAnswer:
    """Start the interview when the owner first links a chat, unless they've had one before."""
    had = await session.scalar(select(CatchUp.id).where(CatchUp.user_id == owner).limit(1))
    if had is not None:
        return CatchUpAnswer(())
    return await start(session, owner, chat_id)


async def answer(
    session: AsyncSession, interview: CatchUp, chat_id: int, body: str, sent_at: datetime
) -> tuple[uuid.UUID, CatchUpAnswer]:
    """Take the owner's message as the answer to the waiting question. Returns the new entry,
    closed already, that the caller adds the message to, and the bot's answer."""
    question = interview.question
    assert question is not None, "an interview that asks has a question"
    entry = JournalEntry(
        user_id=interview.user_id,
        opened_at=sent_at,
        last_message_at=sent_at,
        closed_at=sent_at,
        closed_by="catchup",
        question=question,
    )
    session.add(entry)
    await session.flush()
    calls = _without_buttons(chat_id, interview)
    _next(interview, Turn(question=question, answer=body))
    return entry.id, CatchUpAnswer(calls, ready=entry.id, catch_up=interview.id)


def skip(interview: CatchUp, chat_id: int) -> CatchUpAnswer:
    """Answer `/skip`: move on to the next question."""
    calls = (send(chat_id, SKIPPED), *_without_buttons(chat_id, interview))
    assert interview.question is not None, "an interview that asks has a question"
    _next(interview, Turn(question=interview.question))
    return CatchUpAnswer(calls, catch_up=interview.id)


def pause(interview: CatchUp, chat_id: int) -> tuple[BotCall, ...]:
    """Pause the interview, keeping its question to ask again, and remove the question's
    buttons. Returns those calls."""
    calls = _without_buttons(chat_id, interview)
    interview.status, interview.question_id = "paused", None
    return calls


async def press(session: AsyncSession, owner: uuid.UUID, query: CallbackQuery) -> CatchUpAnswer:
    """Answer a press of a question's Skip or Stop for now button."""
    message = query.message
    interview = await asking(session, owner)
    if message is None or interview is None or interview.question_id != message.message_id:
        return CatchUpAnswer((_answer(query, NOT_WAITING),))
    chat_id = message.chat.id
    if query.data == SKIP_QUESTION:
        skipped = skip(interview, chat_id)
        return CatchUpAnswer((_answer(query), *skipped.calls), catch_up=skipped.catch_up)
    return CatchUpAnswer((_answer(query), *pause(interview, chat_id), send(chat_id, PAUSED)))


async def next_message(
    session: AsyncSession,
    catch_up_id: uuid.UUID,
    client: LLMClient,
    *,
    now: datetime,
) -> Question | None:
    """The message to send for an interview that's writing its next question: the question it
    has waiting, a new one, or the interview's end. Marks it asking as of `now`, or finished.
    None when there's nothing to send: it isn't writing a question, or there's no chat to ask in,
    which pauses it."""
    interview = await session.scalar(
        select(CatchUp).where(CatchUp.id == catch_up_id).with_for_update()
    )
    if interview is None or interview.status != "thinking":
        return None
    chat_id = await chat_of(session, interview.user_id)
    if chat_id is None:
        interview.status = "paused"
        return None
    turns = turns_of(interview)
    question = interview.question
    if question is None:
        version = await latest_version(session, interview.user_id)
        profile = version.profile if version is not None else None
        question = await asyncio.to_thread(next_question, profile, turns, client)
    if question is None:
        interview.status, interview.finished_at = "finished", now
        answered = any(turn.answer is not None for turn in turns)
        return Question(chat_id, FINISHED if answered else NO_ANSWERS, {}, waits=False)
    interview.status, interview.question, interview.asked_at = "asking", question, now
    return Question(chat_id, question, {"reply_markup": KEYBOARD}, waits=True)


async def record_question(
    session: AsyncSession, catch_up_id: uuid.UUID, message_id: int | None
) -> None:
    """Keep the ID of the message that asked, so that its buttons answer for the interview. When
    the bot couldn't send it, the interview pauses, to ask again on `/catchup`."""
    interview = await session.get(CatchUp, catch_up_id, with_for_update=True)
    if interview is None:
        return
    if message_id is None:
        interview.status = "paused"
    else:
        interview.question_id = message_id


async def fail(session: AsyncSession, catch_up_id: uuid.UUID) -> uuid.UUID | None:
    """Pause an interview whose next question couldn't be written. Returns its owner."""
    interview = await session.get(CatchUp, catch_up_id, with_for_update=True)
    if interview is None or interview.status != "thinking":
        return None
    interview.status = "paused"
    return interview.user_id


def failure_text(reason: str) -> str:
    """What the bot says when it can't write the next question."""
    return (
        f"I couldn't think of the next catch-up question because {reason}. "
        "Send /catchup to try again."
    )


def _next(interview: CatchUp, turn: Turn) -> None:
    """Record the turn, and have the job write the next question."""
    interview.transcript = _turns.dump_json([*turns_of(interview), turn]).decode()
    interview.question, interview.question_id, interview.asked_at = None, None, None
    interview.status = "thinking"


def _answer(query: CallbackQuery, text: str | None = None) -> BotCall:
    """Tell Telegram the press was handled, with a short notice if there's something to say."""
    params: dict[str, Any] = {"callback_query_id": query.id}
    if text is not None:
        params["text"] = text
    return BotCall("answerCallbackQuery", params)


def _without_buttons(chat_id: int, interview: CatchUp) -> tuple[BotCall, ...]:
    """Remove the question's buttons, once it no longer waits."""
    if interview.question_id is None:
        return ()
    params = {"chat_id": chat_id, "message_id": interview.question_id}
    return (BotCall("editMessageReplyMarkup", params),)
