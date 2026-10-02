import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import NOT_PER_USER, Base, Setting, User, UserSession


def test_every_table_belongs_to_a_user() -> None:
    """Constraint C4: every row belongs to a user, so more users can come later."""
    for table in Base.metadata.sorted_tables:
        if table.name in NOT_PER_USER:
            continue
        assert "user_id" in table.c, f"{table.name} has no user_id"
        user_id = table.c.user_id
        assert not user_id.nullable, f"{table.name}.user_id can be empty"
        assert {key.target_fullname for key in user_id.foreign_keys} == {"users.id"}


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
