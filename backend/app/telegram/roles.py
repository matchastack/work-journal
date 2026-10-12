"""Which role a journal entry is about (FR-CAP-11).

When an entry closes, the `extract_entry` job (`app/jobs.py`) asks first (`role_question`). The
question replies to the entry's first message, with a button for each role in the owner's latest
profile version and a New role button. Nothing is extracted until the owner answers:

- A role's button links the entry to that role. The question then shows the choice instead of its
  buttons.
- New role asks for the role's title and organisation. The owner's next message, unless it's a
  command, is its name: it's stored encrypted with the entry, and isn't journaled. The question's
  buttons still work until then.

Either answer lets the entry's jobs run at once (`Answer.chosen`). A button on a question about an
entry that already has its role, or that's gone, changes nothing. Role names are never logged.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JournalEntry, JournalMessage
from app.db.profiles import latest_version
from app.schema.profile import Role
from app.telegram.api import BotCall, send
from app.telegram.processing import chat_of, entry_text
from app.telegram.updates import CallbackQuery

NEW_ROLE = "+new"
"""The New role button's data. The other buttons carry a role's ID, which never starts with "+"
and fits in the 64 bytes Telegram allows."""
QUESTION = "Which role is this entry about?"
NO_ROLES = "Which role is this entry about? Your profile has no roles yet, so add a new one."
NAME_PROMPT = (
    'What\'s the new role? Send its title and organisation, such as "Data Engineer, Example Co".'
)
NAME_LIMIT = 100
"""The longest name a new role may have, in characters."""
NAME_TOO_LONG = (
    "That's too long for a role's name, so I didn't save it. Send just its title and "
    "organisation, in one line."
)
ANSWERED = "This entry already has its role."
GONE = "I can't find the entry this question is about."
NOT_IN_PROFILE = "That role isn't in your profile any more. Pick another, or add a new one."


@dataclass(frozen=True)
class RoleQuestion:
    """The question to send about a closed entry."""

    chat_id: int | None
    """The owner's linked chat, or None when there's none to ask in."""
    text: str
    options: dict[str, Any]
    """`sendMessage`'s other parameters: the buttons, and the message the question replies to."""


@dataclass(frozen=True)
class Answer:
    """The bot's answer to a button press or to a new role's name."""

    calls: tuple[BotCall, ...]
    chosen: uuid.UUID | None = None
    """The entry whose role was just chosen, so that its jobs can run."""


def role_label(role: Role) -> str:
    """How a role appears on its button, e.g. `Engineer, Northwind Traders (2025-now)`."""
    end = role.end_date[:4] if role.end_date else "now"
    return f"{role.position}, {role.name} ({role.start_date[:4]}-{end})"


def chosen_text(label: str) -> str:
    """The question once answered, without its buttons."""
    return f"Role for this entry: {label}"


def keyboard(roles: Sequence[Role]) -> dict[str, Any]:
    """A button for each role, in the profile's order, and then New role."""
    rows = [[{"text": role_label(role), "callback_data": role.id}] for role in roles]
    rows.append([{"text": "New role", "callback_data": NEW_ROLE}])
    return {"inline_keyboard": rows}


async def role_question(
    session: AsyncSession, entry_id: uuid.UUID, *, now: datetime
) -> RoleQuestion | None:
    """The question to ask about the entry, if it's closed, waits for its role and hasn't been
    asked about yet. Marks it asked as of `now`."""
    entry = await session.scalar(
        select(JournalEntry).where(JournalEntry.id == entry_id).with_for_update()
    )
    if entry is None or entry.closed_at is None or entry.role_asked_at is not None:
        return None
    if entry.processed_at is not None or entry.failed_at is not None or has_role(entry):
        return None
    if not await entry_text(session, entry_id):
        return None
    entry.role_asked_at = now
    version = await latest_version(session, entry.user_id)
    roles = version.profile.work if version else ()
    chat_id = await chat_of(session, entry.user_id)
    options: dict[str, Any] = {"reply_markup": keyboard(roles)}
    first = (
        await session.execute(
            select(JournalMessage.chat_id, JournalMessage.message_id)
            .where(JournalMessage.entry_id == entry_id, JournalMessage.sender == "owner")
            .order_by(JournalMessage.sent_at, JournalMessage.id)
            .limit(1)
        )
    ).first()
    if first is not None and first.chat_id == chat_id:
        options["reply_parameters"] = {
            "message_id": first.message_id,
            "allow_sending_without_reply": True,
        }
    return RoleQuestion(chat_id, QUESTION if roles else NO_ROLES, options)


async def record_question(session: AsyncSession, entry_id: uuid.UUID, message_id: int) -> None:
    """Keep the ID of the message that asked, so that its buttons answer for the entry."""
    await session.execute(
        update(JournalEntry).where(JournalEntry.id == entry_id).values(role_question_id=message_id)
    )


def has_role(entry: JournalEntry) -> bool:
    return entry.role_id is not None or entry.new_role is not None


async def press(session: AsyncSession, owner: uuid.UUID, query: CallbackQuery) -> Answer:
    """Answer a press of one of the question's buttons."""
    message = query.message
    entry = None
    if message is not None:
        entry = await session.scalar(
            select(JournalEntry)
            .where(
                JournalEntry.user_id == owner,
                JournalEntry.role_question_id == message.message_id,
                JournalEntry.processed_at.is_(None),
                JournalEntry.failed_at.is_(None),
            )
            .with_for_update()
        )
    if message is None or entry is None:
        return Answer((_answer(query, GONE),))
    if has_role(entry):
        return Answer((_answer(query, ANSWERED),))
    chat_id = message.chat.id
    if query.data == NEW_ROLE:
        await session.execute(
            update(JournalEntry)
            .where(JournalEntry.user_id == owner, JournalEntry.naming_role)
            .values(naming_role=False)
        )
        entry.naming_role = True
        prompt = send(
            chat_id,
            NAME_PROMPT,
            reply_markup={"force_reply": True, "input_field_placeholder": "Title, organisation"},
        )
        return Answer((_answer(query), prompt))
    version = await latest_version(session, owner)
    roles = version.profile.work if version else ()
    role = next((role for role in roles if role.id == query.data), None)
    if role is None:
        return Answer((_answer(query, NOT_IN_PROFILE),))
    entry.role_id, entry.naming_role = role.id, False
    shown = _edit(chat_id, message.message_id, chosen_text(role_label(role)))
    return Answer((_answer(query), shown), chosen=entry.id)


async def naming_entry(session: AsyncSession, owner: uuid.UUID) -> JournalEntry | None:
    """The entry whose new role the owner's next message names, if one waits for it."""
    return await session.scalar(
        select(JournalEntry)
        .where(
            JournalEntry.user_id == owner,
            JournalEntry.naming_role,
            JournalEntry.processed_at.is_(None),
            JournalEntry.failed_at.is_(None),
        )
        .with_for_update()
    )


def name_role(entry: JournalEntry, chat_id: int, body: str) -> Answer:
    """Give the entry the new role the owner's message names, if it's one short line."""
    name = " ".join(body.split())
    if not name or "\n" in body.strip() or len(name) > NAME_LIMIT:
        return Answer((send(chat_id, NAME_TOO_LONG),))
    entry.new_role, entry.naming_role = name, False
    calls = [send(chat_id, f"New role for this entry: {name}.")]
    if entry.role_question_id is not None:
        calls.append(_edit(chat_id, entry.role_question_id, chosen_text(f"{name} (new)")))
    return Answer(tuple(calls), chosen=entry.id)


def _answer(query: CallbackQuery, text: str | None = None) -> BotCall:
    """Tell Telegram the press was handled, which ends the button's spinner, with a short notice
    if there's something to say."""
    params: dict[str, Any] = {"callback_query_id": query.id}
    if text is not None:
        params["text"] = text
    return BotCall("answerCallbackQuery", params)


def _edit(chat_id: int, message_id: int, text: str) -> BotCall:
    """Replace a message's text. Its buttons go with it."""
    return BotCall("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text})
