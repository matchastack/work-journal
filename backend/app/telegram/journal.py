"""Turning Telegram updates into journal messages (FR-JRN-1, FR-JRN-3, FR-CAP-2).

`handle_update` is shared by the webhook and by `wj telegram poll`. It stores the raw update
first, so nothing is lost, then the journal message in it. Only private chats linked to a user
become journal messages, grouped into entries (`app/telegram/entries.py`). `/start <token>` from
a link to the bot links a chat (`app/telegram/linking.py`); any other unlinked chat is told how
to link itself. Message text is never logged.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JournalMessage, JournalMessageEdit, TelegramLink, TelegramUpdate
from app.telegram.entries import DEFAULT_TIMEOUT, entry_for
from app.telegram.linking import link_chat, start_token
from app.telegram.updates import Message, Update

LINK_HINT = (
    "This bot keeps a private work journal. To journal here, open the link to this bot from "
    "your Work Journal account."
)


@dataclass(frozen=True)
class Reply:
    """A message the bot sends back to a chat."""

    chat_id: int
    text: str

    def webhook_body(self) -> dict[str, object]:
        """The reply as a Bot API call in the webhook's answer, which Telegram makes itself."""
        return {"method": "sendMessage", "chat_id": self.chat_id, "text": self.text}


async def handle_update(
    session: AsyncSession, payload: str, *, entry_timeout: timedelta = DEFAULT_TIMEOUT
) -> Reply | None:
    """Store an update from Telegram, given as the JSON it arrived as, and the journal message in
    it. Returns the bot's reply, if any. An update Telegram sent before is ignored.

    Raises `pydantic.ValidationError` when the payload isn't an update.
    """
    update = Update.model_validate_json(payload)
    if not await _store_raw(session, update.update_id, payload):
        return None
    if update.message is not None:
        return await _new_message(session, update.message, entry_timeout)
    if update.edited_message is not None:
        await _edit(session, update.edited_message)
    return None


async def purge_updates(session: AsyncSession, *, before: datetime) -> int:
    """Delete the raw updates received before `before` (FR-JRN-2). Returns how many there were."""
    result = await session.execute(
        delete(TelegramUpdate)
        .where(TelegramUpdate.received_at < before)
        .returning(TelegramUpdate.update_id)
    )
    return len(result.all())


async def _store_raw(session: AsyncSession, update_id: int, payload: str) -> bool:
    """Keep the raw update. False when it was kept before: Telegram resent it."""
    statement = (
        insert(TelegramUpdate)
        .values(update_id=update_id, payload=payload)
        .on_conflict_do_nothing(index_elements=[TelegramUpdate.update_id])
        .returning(TelegramUpdate.update_id)
    )
    return (await session.execute(statement)).scalar_one_or_none() is not None


async def _chat_owner(session: AsyncSession, chat_id: int) -> uuid.UUID | None:
    statement = select(TelegramLink.user_id).where(TelegramLink.chat_id == chat_id)
    return await session.scalar(statement)


async def _new_message(
    session: AsyncSession, message: Message, entry_timeout: timedelta
) -> Reply | None:
    if message.chat.type != "private":
        return None
    if message.text is not None and (token := start_token(message.text)) is not None:
        return Reply(message.chat.id, await link_chat(session, message.chat.id, token))
    owner = await _chat_owner(session, message.chat.id)
    if owner is None:
        return Reply(message.chat.id, LINK_HINT)
    body = message.body
    if body is None or body.startswith("/"):
        return None
    entry_id = await entry_for(session, owner, message.sent_at, entry_timeout)
    statement = (
        insert(JournalMessage)
        .values(
            user_id=owner,
            chat_id=message.chat.id,
            message_id=message.message_id,
            entry_id=entry_id,
            sender="owner",
            text=body,
            sent_at=message.sent_at,
        )
        .on_conflict_do_nothing(index_elements=[JournalMessage.chat_id, JournalMessage.message_id])
    )
    await session.execute(statement)
    return None


async def _edit(session: AsyncSession, message: Message) -> None:
    """Save the new text of an edited journal message, and keep the one it replaces. An edit
    older than the stored text, delivered late, is ignored."""
    body = message.body
    if body is None:
        return
    row = await session.scalar(
        select(JournalMessage).where(
            JournalMessage.chat_id == message.chat.id,
            JournalMessage.message_id == message.message_id,
        )
    )
    if row is None or row.text == body:
        return
    edited_at = message.edited_at
    if row.edited_at is not None and edited_at <= row.edited_at:
        return
    session.add(
        JournalMessageEdit(
            user_id=row.user_id, message_id=row.id, text=row.text, replaced_at=edited_at
        )
    )
    row.text = body
    row.edited_at = edited_at
