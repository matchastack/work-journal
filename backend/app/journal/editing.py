"""Corrections to the journal in the web app (FR-JRN-6).

- A message's text can be edited. The text it replaces is kept, as when it's edited in Telegram
  (FR-JRN-3). The facts already saved from its entry stay as they are; the owner corrects them
  separately.
- A message can be deleted, unless it's its entry's only one: then the owner deletes the entry.
  Telegram doesn't tell bots about deleted messages (C3), so this is where they're deleted.
- An entry can be deleted, with its messages and the facts saved from it.

Deleting facts that the latest profile version cites needs confirming (`FactsCited`): the
bullets keep their text, but lose the facts that back them. Text is never logged.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FactRow, JournalEntry, JournalMessage, JournalMessageEdit
from app.journal.document import (
    Citation,
    JournalMessageView,
    MessageEdit,
    citations,
    latest_profile,
)
from app.text import plain


class NotFound(LookupError):
    """The owner has no such message, entry or fact."""


class OnlyMessage(ValueError):
    """The message is its entry's only one."""


class FactsCited(ValueError):
    """Facts to delete are cited by bullets in the latest profile version."""

    def __init__(self, cited: Sequence[Citation]) -> None:
        self.cited = tuple(cited)
        count = len(self.cited)
        super().__init__(
            f"{count} {'bullet' if count == 1 else 'bullets'} in your profile "
            f"{'cites' if count == 1 else 'cite'} this; confirm to delete anyway"
        )


async def edit_message(
    session: AsyncSession, user_id: uuid.UUID, message_id: uuid.UUID, text: str, *, now: datetime
) -> JournalMessageView:
    """Replace a message's text as of `now`, keeping the text it replaces. Text that's the same,
    once both are in ASCII characters, changes nothing."""
    message = await _message(session, user_id, message_id)
    if plain(message.text) != text:
        session.add(
            JournalMessageEdit(
                user_id=user_id, message_id=message.id, text=message.text, replaced_at=now
            )
        )
        message.text, message.edited_at = text, now
        await session.flush()
    edits = await session.execute(
        select(JournalMessageEdit.text, JournalMessageEdit.replaced_at)
        .where(JournalMessageEdit.message_id == message.id)
        .order_by(JournalMessageEdit.replaced_at, JournalMessageEdit.id)
    )
    return JournalMessageView(
        id=message.id,
        sent_at=message.sent_at,
        edited_at=message.edited_at,
        text=message.text,
        edits=tuple(MessageEdit(text=text, replaced_at=at) for text, at in edits),
    )


async def delete_message(session: AsyncSession, user_id: uuid.UUID, message_id: uuid.UUID) -> None:
    """Delete a message and its earlier text. Raises `OnlyMessage` for an entry's only one."""
    message = await _message(session, user_id, message_id)
    others = await session.scalar(
        select(func.count(JournalMessage.id)).where(
            JournalMessage.entry_id == message.entry_id,
            JournalMessage.sender == "owner",
            JournalMessage.id != message.id,
        )
    )
    if not others:
        raise OnlyMessage("this is the entry's only message; delete the entry instead")
    await session.execute(delete(JournalMessage).where(JournalMessage.id == message.id))


async def delete_entry(
    session: AsyncSession, user_id: uuid.UUID, entry_id: uuid.UUID, *, confirm: bool = False
) -> None:
    """Delete an entry, its messages and the facts saved from it. Raises `FactsCited` when the
    profile cites one of the facts, unless `confirm`."""
    entry = await session.scalar(
        select(JournalEntry.id)
        .where(JournalEntry.user_id == user_id, JournalEntry.id == entry_id)
        .with_for_update()
    )
    if entry is None:
        raise NotFound(f"there's no entry {entry_id}")
    facts = list(
        await session.scalars(
            select(FactRow.id).where(FactRow.user_id == user_id, FactRow.entry_id == str(entry_id))
        )
    )
    await _check_citations(session, user_id, facts, confirm=confirm)
    await session.execute(
        delete(FactRow).where(FactRow.user_id == user_id, FactRow.entry_id == str(entry_id))
    )
    # Its messages, and their earlier text, go with it.
    await session.execute(delete(JournalEntry).where(JournalEntry.id == entry_id))


async def _message(
    session: AsyncSession, user_id: uuid.UUID, message_id: uuid.UUID
) -> JournalMessage:
    message = await session.scalar(
        select(JournalMessage)
        .where(
            JournalMessage.user_id == user_id,
            JournalMessage.id == message_id,
            JournalMessage.sender == "owner",
        )
        .with_for_update()
    )
    if message is None:
        raise NotFound(f"there's no message {message_id}")
    return message


async def _check_citations(
    session: AsyncSession, user_id: uuid.UUID, fact_ids: Sequence[str], *, confirm: bool
) -> None:
    if confirm or not fact_ids:
        return
    cited = citations(await latest_profile(session, user_id))
    found = [citation for fact_id in fact_ids for citation in cited.get(fact_id, ())]
    if found:
        raise FactsCited(list({citation.bullet_id: citation for citation in found}.values()))
