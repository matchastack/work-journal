"""The connection to PostgreSQL: an async SQLAlchemy engine using asyncpg."""

import asyncio
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DatabaseStatus = Literal["ok", "unreachable", "not configured"]
HEALTH_TIMEOUT_S = 2.0
_PLAIN_SCHEMES = ("postgresql://", "postgres://")


def async_url(url: str) -> str:
    """The URL with the asyncpg driver, as hosts such as Railway give plain `postgresql://`."""
    for scheme in _PLAIN_SCHEMES:
        if url.startswith(scheme):
            return "postgresql+asyncpg://" + url.removeprefix(scheme)
    return url


def create_engine(url: str) -> AsyncEngine:
    return create_async_engine(async_url(url), pool_pre_ping=True)


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def check_database(engine: AsyncEngine | None) -> DatabaseStatus:
    """Whether the database answers a trivial query within a couple of seconds."""
    if engine is None:
        return "not configured"
    try:
        async with asyncio.timeout(HEALTH_TIMEOUT_S), engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (TimeoutError, OSError, SQLAlchemyError):
        return "unreachable"
    return "ok"
