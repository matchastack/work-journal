from collections.abc import AsyncIterator

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.crypto import (
    DecryptionError,
    EncryptedText,
    KeyRing,
    new_key,
    rotate_keys,
    use_key_ring,
)

pytestmark = pytest.mark.anyio

METADATA = MetaData()
NOTES = Table(
    "test_notes",
    METADATA,
    Column("id", Integer, primary_key=True),
    Column("body", EncryptedText("test_notes.body")),
)
KEY_1 = new_key("k1")
KEY_2 = new_key("k2")


@pytest.fixture
async def notes(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """A session with a table that has an encrypted column, rolled back after the test."""
    use_key_ring(KeyRing.parse(KEY_1))
    connection = await session.connection()
    await connection.run_sync(METADATA.create_all)
    yield session
    use_key_ring(None)


async def test_text_is_ciphertext_at_rest(notes: AsyncSession) -> None:
    await notes.execute(insert(NOTES).values(id=1, body="Shipped the export page."))
    raw = await notes.scalar(text("SELECT body FROM test_notes WHERE id = 1"))
    assert isinstance(raw, bytes)
    assert b"export" not in raw
    body = await notes.scalar(select(NOTES.c.body).where(NOTES.c.id == 1))
    assert body == "Shipped the export page."


async def test_the_wrong_key_fails_clearly(notes: AsyncSession) -> None:
    await notes.execute(insert(NOTES).values(id=1, body="Shipped."))
    use_key_ring(KeyRing.parse("k1:" + new_key("x").split(":")[1]))
    with pytest.raises(DecryptionError, match="doesn't match key 'k1'"):
        await notes.scalar(select(NOTES.c.body))


async def test_rotation_re_encrypts_with_the_new_key(notes: AsyncSession) -> None:
    rows = [{"id": 1, "body": "One."}, {"id": 2, "body": "Two."}, {"id": 3, "body": None}]
    await notes.execute(insert(NOTES).values(rows))
    use_key_ring(KeyRing.parse(f"{KEY_2},{KEY_1}"))
    assert await rotate_keys(notes, METADATA) == {"test_notes.body": 2}
    use_key_ring(KeyRing.parse(KEY_2))  # the old key can go now
    bodies = await notes.scalars(select(NOTES.c.body).order_by(NOTES.c.id))
    assert bodies.all() == ["One.", "Two.", None]
    assert await rotate_keys(notes, METADATA) == {"test_notes.body": 0}


async def test_rotation_without_the_old_key_changes_nothing(notes: AsyncSession) -> None:
    await notes.execute(insert(NOTES).values(id=1, body="One."))
    use_key_ring(KeyRing.parse(KEY_2))
    with pytest.raises(DecryptionError, match="no key 'k1'"):
        await rotate_keys(notes, METADATA)
