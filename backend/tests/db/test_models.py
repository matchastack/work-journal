import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import (
    NOT_PER_USER,
    Base,
    JournalMessage,
    JournalMessageEdit,
    Setting,
    TelegramLink,
    User,
    UserSession,
)


def test_every_table_belongs_to_a_user() -> None:
    """Constraint C4: every row belongs to a user, so more users can come later."""
    for table in Base.metadata.sorted_tables:
        if table.name in NOT_PER_USER:
            continue
        assert "user_id" in table.c, f"{table.name} has no user_id"
        user_id = table.c.user_id
        assert not user_id.nullable, f"{table.name}.user_id can be empty"
        # It can also be part of a key to another table, such as an application's PDF.
        assert "users.id" in {key.target_fullname for key in user_id.foreign_keys}


async def new_user(session: AsyncSession) -> User:
    user = User()
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


@pytest.mark.anyio
async def test_a_user_keeps_settings_by_key(session: AsyncSession) -> None:
    user = await new_user(session)
    session.add(Setting(user_id=user.id, key="style_notes", value={"avoid": ["leverage"]}))
    await session.commit()
    stored = await session.get(Setting, (user.id, "style_notes"))
    assert stored is not None
    assert stored.value == {"avoid": ["leverage"]}
    assert user.created_at.tzinfo is not None


@pytest.mark.anyio
async def test_each_key_is_stored_once_per_user(session: AsyncSession) -> None:
    user = await new_user(session)
    row = {"user_id": user.id, "key": "reminders", "value": {}}
    await session.execute(insert(Setting).values(row))
    with pytest.raises(IntegrityError):
        await session.execute(insert(Setting).values(row))


@pytest.mark.anyio
async def test_deleting_a_user_deletes_their_settings(session: AsyncSession) -> None:
    user = await new_user(session)
    session.add(Setting(user_id=user.id, key="reminders", value={"paused": True}))
    await session.flush()
    await session.execute(delete(User).where(User.id == user.id))
    count = select(func.count()).select_from(Setting).where(Setting.user_id == user.id)
    assert await session.scalar(count) == 0


@pytest.mark.anyio
async def test_a_github_account_belongs_to_one_user(session: AsyncSession) -> None:
    await session.execute(insert(User).values(github_id=583231, github_login="octo"))
    with pytest.raises(IntegrityError):
        await session.execute(insert(User).values(github_id=583231, github_login="octo-2"))


@pytest.mark.anyio
async def test_users_without_github_are_allowed(session: AsyncSession) -> None:
    """Other ways to sign in come later (FR-AUTH-3), so GitHub columns may be empty."""
    first, second = await new_user(session), await new_user(session)
    assert first.github_id is None
    assert second.github_id is None


@pytest.mark.anyio
async def test_deleting_a_user_deletes_their_sessions(session: AsyncSession) -> None:
    user = await new_user(session)
    session.add(
        UserSession(
            token_hash=hashlib.sha256(b"token").digest(),
            user_id=user.id,
            csrf_token="csrf",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    await session.flush()
    await session.execute(delete(User).where(User.id == user.id))
    count = select(func.count()).select_from(UserSession).where(UserSession.user_id == user.id)
    assert await session.scalar(count) == 0


@pytest.fixture
def key_ring() -> Iterator[None]:
    use_key_ring(KeyRing.parse(new_key("k1")))
    yield
    use_key_ring(None)


def message(
    user: User, message_id: int, body: str = "Moved the nightly export to a queue."
) -> JournalMessage:
    return JournalMessage(
        user_id=user.id,
        chat_id=4242,
        message_id=message_id,
        sender="owner",
        text=body,
        sent_at=datetime(2026, 10, 4, 9, 0, tzinfo=UTC),
    )


@pytest.mark.anyio
async def test_a_telegram_chat_is_linked_to_one_user(session: AsyncSession) -> None:
    first, second = await new_user(session), await new_user(session)
    await session.execute(insert(TelegramLink).values(user_id=first.id, chat_id=4242))
    with pytest.raises(IntegrityError):
        await session.execute(insert(TelegramLink).values(user_id=second.id, chat_id=4242))


@pytest.mark.anyio
@pytest.mark.usefixtures("key_ring")
async def test_journal_text_is_stored_encrypted_once_per_message(session: AsyncSession) -> None:
    """FR-JRN-4: a database dump shows only ciphertext. Telegram numbers messages per chat."""
    user = await new_user(session)
    session.add(message(user, 7))
    await session.flush()
    raw = await session.scalar(text("SELECT text FROM journal_messages WHERE message_id = 7"))
    assert b"nightly export" not in bytes(raw)
    session.add(message(user, 7, "Another text with the same number."))
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.anyio
@pytest.mark.usefixtures("key_ring")
async def test_deleting_a_message_deletes_its_earlier_texts(session: AsyncSession) -> None:
    user = await new_user(session)
    row = message(user, 8)
    session.add(row)
    await session.flush()
    session.add(
        JournalMessageEdit(
            user_id=user.id,
            message_id=row.id,
            text="Moved the export.",
            replaced_at=datetime(2026, 10, 4, 9, 5, tzinfo=UTC),
        )
    )
    await session.flush()
    await session.execute(delete(JournalMessage).where(JournalMessage.id == row.id))
    edits = select(func.count()).select_from(JournalMessageEdit)
    assert await session.scalar(edits.where(JournalMessageEdit.user_id == user.id)) == 0
