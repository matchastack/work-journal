import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Literal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import JournalEntry, JournalMessage, ProfileVersionRow, User

VERSIONS = Path(__file__).parents[2] / "migrations" / "versions"


def migration(name: str) -> ModuleType:
    """A migration's module, such as `0007_journal_entries`."""
    spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migrations_match_the_models(alembic: Config) -> None:
    """Fails when a model changes without a migration (`alembic revision --autogenerate`)."""
    command.check(alembic)


def test_the_migrations_go_down_and_up_again(alembic: Config) -> None:
    command.downgrade(alembic, "base")
    command.upgrade(alembic, "head")


@pytest.mark.anyio
async def test_profile_versions_are_immutable(session: AsyncSession) -> None:
    """NFR-DATA-1: the database itself refuses to change a saved version."""
    user = await session.scalar(insert(User).returning(User.id))
    version = {"user_id": user, "number": 1, "author": "owner", "profile": {"basics": {}}}
    await session.execute(insert(ProfileVersionRow).values(version))
    with pytest.raises(DBAPIError, match="profile versions are immutable"):
        async with session.begin_nested():
            await session.execute(update(ProfileVersionRow).values(author="ai"))


def journal_message(
    user: uuid.UUID, number: int, sender: Literal["owner", "bot"], at: datetime
) -> JournalMessage:
    return JournalMessage(
        user_id=user, chat_id=4242, message_id=number, sender=sender, text="A note.", sent_at=at
    )


@pytest.mark.anyio
async def test_messages_from_before_entries_are_grouped(session: AsyncSession) -> None:
    """Migration 0007 groups the messages already journaled by the 30-minute rule. It closes the
    entries that have been quiet for 30 minutes, and leaves a more recent one open."""
    nine = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    sent = [
        nine,
        nine + timedelta(minutes=29, seconds=59),
        nine + timedelta(minutes=59, seconds=59),
        nine + timedelta(hours=2),
        datetime.now(UTC) - timedelta(minutes=5),
    ]
    user = await session.scalar(insert(User).returning(User.id))
    assert user is not None
    use_key_ring(KeyRing.parse(new_key("k1")))
    try:
        for number, at in enumerate(sent, start=1):
            session.add(journal_message(user, number, "owner", at))
        session.add(journal_message(user, len(sent) + 1, "bot", nine))
        await session.flush()
        await session.execute(text(migration("0007_journal_entries").GROUP_EARLIER_MESSAGES))
    finally:
        use_key_ring(None)
    rows = (
        await session.execute(
            select(JournalMessage.entry_id, JournalEntry.closed_at, JournalEntry.closed_by)
            .outerjoin(JournalEntry, JournalEntry.id == JournalMessage.entry_id)
            .where(JournalMessage.user_id == user)
            .order_by(JournalMessage.message_id)
        )
    ).all()
    entries = [row.entry_id for row in rows]
    assert entries[0] == entries[1], "29:59 apart: the same entry"
    assert len(set(entries[:5])) == 4, "30:00 apart or more: a new entry"
    assert entries[5] is None, "the bot's own messages belong to no entry"
    assert [(row.closed_at, row.closed_by) for row in rows[1:5]] == [
        (sent[1] + timedelta(minutes=30), "quiet"),
        (sent[2] + timedelta(minutes=30), "quiet"),
        (sent[3] + timedelta(minutes=30), "quiet"),
        (None, None),
    ], "closed as the tick would close them; the recent one stays open"
