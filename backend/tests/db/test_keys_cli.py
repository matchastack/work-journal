import asyncio
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from typer.testing import CliRunner

from app.cli import cli
from app.config import get_settings
from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import FactRow, User

FACT = '{"id": "nw_export", "statement": "Cut the nightly export job to 12 minutes."}'


def run[T](database_url: str, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Do `work` in a session of its own, and commit it."""

    async def go() -> T:
        engine = create_async_engine(database_url)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                result = await work(session)
                await session.commit()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_keys_rotate_on_the_database(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    old, new = new_key("k1"), new_key("k2")

    async def add_fact(session: AsyncSession) -> uuid.UUID:
        user = User()
        session.add(user)
        await session.flush()
        await session.execute(
            insert(FactRow).values(user_id=user.id, id="nw_export", data=FACT, origin="journal")
        )
        return user.id

    use_key_ring(KeyRing.parse(old))
    user_id = run(database_url, add_fact)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", f"{new},{old}")
    get_settings.cache_clear()
    use_key_ring(None)
    try:
        result = CliRunner().invoke(cli, ["keys", "rotate"])
        use_key_ring(KeyRing.parse(new))
        stored = select(FactRow.data).where(FactRow.user_id == user_id)
        assert run(database_url, lambda session: session.scalar(stored)) == FACT
    finally:
        run(database_url, lambda session: session.execute(delete(User).where(User.id == user_id)))
        get_settings.cache_clear()
        use_key_ring(None)
    assert result.exit_code == 0, result.output
    assert "facts.data: re-encrypted 1 value\n" in result.stdout
    assert "Everything is under key 'k2'." in result.stdout
