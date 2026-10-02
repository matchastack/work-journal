import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import insert, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProfileVersionRow, User


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
