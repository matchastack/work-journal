"""The connection to PostgreSQL: an async SQLAlchemy engine using asyncpg."""

import asyncio
from collections.abc import Mapping
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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


_LIBPQ_ONLY = frozenset({"channel_binding"})
"""Parameters in hosts' URLs that only libpq knows, such as Neon's `channel_binding`."""


def async_url(url: str) -> str:
    """The URL with the asyncpg driver. Hosts such as Neon give a plain `postgresql://` URL with
    libpq's `sslmode`, which asyncpg calls `ssl`, and parameters asyncpg doesn't take."""
    for scheme in _PLAIN_SCHEMES:
        if url.startswith(scheme):
            url = "postgresql+asyncpg://" + url.removeprefix(scheme)
            break
    return with_query(url, rename={"sslmode": "ssl"}, drop=_LIBPQ_ONLY)


def with_query(
    url: str, *, rename: Mapping[str, str] | None = None, drop: frozenset[str] = frozenset()
) -> str:
    """The URL with its query parameters renamed or dropped."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    rename = rename or {}
    query = [
        (rename.get(name, name), value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if name not in drop
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))


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
