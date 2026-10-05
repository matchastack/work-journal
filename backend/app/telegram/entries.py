"""Grouping journal messages into entries (FR-CAP-4).

A message joins its user's open entry unless the entry has been quiet for the timeout
(`ENTRY_TIMEOUT_MINUTES`, 30 by default): then that entry closes, and the message starts a new
one. A scheduled task closes entries that have gone quiet (`close_quiet_entries` in
`app/jobs.py`), and `/done` closes the open entry at once. A closed entry never reopens: editing
one of its messages in Telegram changes the message, not the entry.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JournalEntry

DEFAULT_TIMEOUT = timedelta(minutes=30)


async def entry_for(
    session: AsyncSession, user_id: uuid.UUID, sent_at: datetime, timeout: timedelta
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """The entry a message sent at `sent_at` belongs to: the open one, or a new one. Also returns
    the entry the message closed, when the open one had gone quiet."""
    entry = await session.scalar(
        select(JournalEntry)
        .where(JournalEntry.user_id == user_id, JournalEntry.closed_at.is_(None))
        .with_for_update()
    )
    if entry is not None:
        if sent_at - entry.last_message_at < timeout:
            entry.last_message_at = max(entry.last_message_at, sent_at)
            return entry.id, None
        entry.closed_at = entry.last_message_at + timeout
        entry.closed_by = "quiet"
        await session.flush()
    closed = entry.id if entry is not None else None
    entry = JournalEntry(user_id=user_id, opened_at=sent_at, last_message_at=sent_at)
    session.add(entry)
    await session.flush()
    return entry.id, closed


async def close_open_entry(
    session: AsyncSession, user_id: uuid.UUID, at: datetime
) -> uuid.UUID | None:
    """Close the user's open entry as of `at`, for `/done`. Returns it, or None when no entry was
    open."""
    result = await session.execute(
        update(JournalEntry)
        .where(JournalEntry.user_id == user_id, JournalEntry.closed_at.is_(None))
        .values(closed_at=at, closed_by="done")
        .returning(JournalEntry.id)
    )
    return result.scalar_one_or_none()


async def close_quiet_entries(
    session: AsyncSession, now: datetime, timeout: timedelta
) -> list[uuid.UUID]:
    """Close every entry that has been quiet for `timeout` by `now`, as of when it went quiet.
    Returns the closed entries."""
    result = await session.execute(
        update(JournalEntry)
        .where(JournalEntry.closed_at.is_(None), JournalEntry.last_message_at <= now - timeout)
        .values(closed_at=JournalEntry.last_message_at + timeout, closed_by="quiet")
        .returning(JournalEntry.id)
    )
    return list(result.scalars())
