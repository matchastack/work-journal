"""Background jobs and scheduled tasks (NFR-REL-1), on Procrastinate and PostgreSQL.

Jobs run in the worker (`wj worker`), outside web requests:
- `@jobs.task(retry=RETRY)` retries a job that raises, with exponential backoff;
- `@jobs.periodic(cron="...")` runs a task on a schedule.

Job arguments are stored as plain JSON and appear in the worker's logs, so pass IDs, never journal
or fact text. Procrastinate's tables come from migration 0002; a newer Procrastinate needs a
migration that applies its new SQL files.
"""

import logging
from typing import Any

import procrastinate
from psycopg_pool import AsyncConnectionPool

from app.config import get_settings

logger = logging.getLogger(__name__)

RETRY = procrastinate.RetryStrategy(max_attempts=5, exponential_wait=4)
"""Up to 5 retries after a failure, 4 s, 16 s, 64 s, 256 s and 1,024 s later (23 minutes in all)."""


def psycopg_url(url: str) -> str:
    """The database URL for psycopg, which the worker uses; the app itself uses asyncpg."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


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


async def run_worker(concurrency: int = 1) -> None:
    """Run queued jobs and scheduled tasks until stopped with Ctrl-C or SIGTERM."""
    async with jobs.open_async():
        await jobs.run_worker_async(concurrency=concurrency, install_signal_handlers=True)
