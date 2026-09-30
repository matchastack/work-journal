"""Fixtures for tests that use PostgreSQL.

TEST_DATABASE_URL points at a server where the user may create databases, such as the one in
compose.yml: `postgresql+asyncpg://work_journal@localhost:5432/work_journal`. Each test session
gets a fresh database, migrated to the latest revision. Each test runs in a transaction that's
rolled back afterwards. Without TEST_DATABASE_URL these tests skip, except in CI, where they must
run.
"""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.engine import async_url

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    server = os.environ.get("TEST_DATABASE_URL")
    if not server:
        message = "set TEST_DATABASE_URL to run the database tests (see compose.yml)"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    server_url = make_url(async_url(server))
    name = f"wj_test_{uuid.uuid4().hex[:12]}"
    admin = server_url.render_as_string(hide_password=False)
    asyncio.run(_execute(admin, f'CREATE DATABASE "{name}"'))
    url = server_url.set(database=name).render_as_string(hide_password=False)
    command.upgrade(_alembic_config(url), "head")
    yield url
    asyncio.run(_execute(admin, f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def alembic(database_url: str) -> Config:
    """Alembic, set up to migrate the test database."""
    return _alembic_config(database_url)


@pytest.fixture
async def session(database_url: str) -> AsyncIterator[AsyncSession]:
    """A session whose changes are rolled back after the test, even if the test commits."""
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


def _alembic_config(url: str) -> Config:
    config = Config(BACKEND / "alembic.ini")
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["configure_logging"] = False
    return config


async def _execute(url: str, statement: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as connection:
        await connection.execute(text(statement))
    await engine.dispose()
