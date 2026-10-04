"""Background jobs and scheduled tasks (NFR-REL-1), on Procrastinate and PostgreSQL.

Jobs run in the worker (`wj worker`), outside web requests:
- `@jobs.task(retry=RETRY)` retries a job that raises, with exponential backoff;
- `@jobs.periodic(cron="...")` runs a task on a schedule.

Job arguments are stored as plain JSON and appear in the worker's logs, so pass IDs, never journal
or fact text. Procrastinate's tables come from migration 0002; a newer Procrastinate needs a
migration that applies its new SQL files.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import procrastinate
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.engine import create_engine, session_factory, with_query
from app.telegram.entries import close_quiet_entries
from app.telegram.journal import purge_updates

logger = logging.getLogger(__name__)

RETRY = procrastinate.RetryStrategy(max_attempts=5, exponential_wait=4)
"""Up to 5 retries after a failure, 4 s, 16 s, 64 s, 256 s and 1,024 s later (23 minutes in all)."""
UPDATE_RETENTION = timedelta(days=7)
"""How long raw Telegram updates are kept (FR-JRN-2)."""


def psycopg_url(url: str) -> str:
    """The database URL for psycopg, which the worker uses; the app itself uses asyncpg. An
    asyncpg-style `ssl` becomes libpq's `sslmode`."""
    return with_query(
        url.replace("postgresql+asyncpg://", "postgresql://", 1), rename={"ssl": "sslmode"}
    )


def _pool(**options: Any) -> AsyncConnectionPool:
    url = get_settings().database_url
    if url is None:
        raise RuntimeError("set DATABASE_URL to run background jobs")
    return AsyncConnectionPool(conninfo=psycopg_url(url.get_secret_value()), **options)


jobs = procrastinate.App(connector=procrastinate.PsycopgConnector(pool_factory=_pool))


@jobs.task(name="echo", retry=RETRY)
async def echo(message: str) -> str:
    """An example job that returns its message, to check the queue end to end."""
    return message


@jobs.periodic(cron="*/15 * * * *")
@jobs.task(name="heartbeat")
async def heartbeat(timestamp: int) -> None:
    """Runs every 15 minutes, so a recent success in the job table shows the worker is alive."""
    logger.info("worker heartbeat")


@jobs.periodic(cron="17 3 * * *")
@jobs.task(name="purge_telegram_updates", retry=RETRY)
async def purge_telegram_updates(timestamp: int) -> None:
    """Delete raw Telegram updates kept for more than 7 days, daily at 03:17 UTC (FR-JRN-2)."""
    before = datetime.fromtimestamp(timestamp, UTC) - UPDATE_RETENTION
    async with database() as session:
        count = await purge_updates(session, before=before)
    logger.info("deleted %d raw Telegram updates", count)


@jobs.periodic(cron="* * * * *")
@jobs.task(name="close_quiet_entries", retry=RETRY)
async def close_quiet_entries_task(timestamp: int) -> None:
    """Close the journal entries that have gone quiet (FR-CAP-4). Due every minute, so it runs on
    every tick, and every minute under `wj worker`."""
    timeout = timedelta(minutes=get_settings().entry_timeout_minutes)
    async with database() as session:
        closed = await close_quiet_entries(session, datetime.fromtimestamp(timestamp, UTC), timeout)
    logger.info("closed %d quiet journal entries", len(closed))


@asynccontextmanager
async def database() -> AsyncGenerator[AsyncSession]:
    """A database session for one job, committed when the block ends without an error."""
    url = get_settings().database_url
    if url is None:
        raise RuntimeError("set DATABASE_URL to run background jobs")
    engine = create_engine(url.get_secret_value())
    try:
        async with session_factory(engine)() as session, session.begin():
            yield session
    finally:
        await engine.dispose()


async def run_worker(concurrency: int = 1) -> None:
    """Run queued jobs and scheduled tasks until stopped with Ctrl-C or SIGTERM."""
    async with jobs.open_async():
        await jobs.run_worker_async(concurrency=concurrency, install_signal_handlers=True)
