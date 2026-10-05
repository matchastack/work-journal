"""Background jobs and scheduled tasks (NFR-REL-1), on Procrastinate and PostgreSQL.

Jobs run in the worker (`wj worker`), outside web requests:
- `@jobs.task(retry=RETRY)` retries a job that raises, with exponential backoff;
- `@jobs.periodic(cron="...")` runs a task on a schedule.

Job arguments are stored as plain JSON and appear in the worker's logs, so pass IDs, never journal
or fact text. Procrastinate's tables come from migration 0002; a newer Procrastinate needs a
migration that applies its new SQL files.
"""

import logging
import uuid
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import procrastinate
from procrastinate.exceptions import AlreadyEnqueued
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.engine import create_engine, session_factory, with_query
from app.db.models import JournalEntry
from app.llm.client import LLMClient, LLMConfigError, LLMInvalidOutput, LLMRefusal
from app.llm.usage import CallLog, DatabaseCallLog
from app.telegram.api import BotApi, TelegramError
from app.telegram.entries import close_quiet_entries
from app.telegram.journal import purge_updates
from app.telegram.processing import chat_of, entry_link, failure_text, process_entry

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
    """Close the journal entries that have gone quiet (FR-CAP-4), then queue every closed entry
    that isn't processed yet (FR-CAP-5). Due every minute, so it runs on every tick, and every
    minute under `wj worker`."""
    timeout = timedelta(minutes=get_settings().entry_timeout_minutes)
    async with database() as session:
        closed = await close_quiet_entries(session, datetime.fromtimestamp(timestamp, UTC), timeout)
    async with database() as session:
        queued = await queue_extractions(session)
    logger.info("closed %d quiet journal entries; queued %d for extraction", len(closed), queued)


@jobs.task(name="extract_entry", retry=RETRY, pass_context=True)
async def extract_entry(context: procrastinate.JobContext, entry_id: str) -> None:
    """Triage a closed journal entry, save its facts and reply to its owner (FR-CAP-5). The owner
    is told when it finally fails."""
    job = context.job
    await run_extraction(
        uuid.UUID(entry_id),
        last_attempt=lambda error: RETRY.get_retry_decision(exception=error, job=job) is None,
    )


async def queue_extractions(session: AsyncSession) -> int:
    """Queue `extract_entry` for each closed entry that's neither processed nor failed, unless
    it's queued already. Jobs for one entry run one at a time. Returns how many were queued."""
    pending = await session.scalars(
        select(JournalEntry.id)
        .where(
            JournalEntry.closed_at.is_not(None),
            JournalEntry.processed_at.is_(None),
            JournalEntry.failed_at.is_(None),
        )
        .order_by(JournalEntry.closed_at)
    )
    queued = 0
    for entry_id in pending:
        job = extract_entry.configure(
            lock=f"journal_entry:{entry_id}", queueing_lock=f"extract_entry:{entry_id}"
        )
        try:
            await job.defer_async(entry_id=str(entry_id))
        except AlreadyEnqueued:
            continue
        queued += 1
    return queued


async def run_extraction(
    entry_id: uuid.UUID, *, last_attempt: Callable[[BaseException], bool]
) -> None:
    """Process the entry (`app/telegram/processing.py`) and send the reply before the result is
    committed. A refusal or an unusable answer won't change on a retry, so it fails at once;
    anything else is retried, and fails when `last_attempt` says this was the last try."""
    async with database() as session:
        user_id = await session.scalar(
            select(JournalEntry.user_id).where(JournalEntry.id == entry_id)
        )
    if user_id is None:
        return
    log = DatabaseCallLog(user_id)
    try:
        async with database() as session:
            processed = await process_entry(
                session,
                entry_id,
                _llm_client(log),
                now=datetime.now(UTC),
                app_url=get_settings().app_url,
            )
            if processed is not None and processed.chat_id is not None:
                await _send(processed.chat_id, processed.reply)
    except (LLMRefusal, LLMInvalidOutput) as error:
        await _give_up(entry_id, error)
    except Exception as error:
        if last_attempt(error):
            await _give_up(entry_id, error)
        raise
    finally:
        async with database() as session:
            await log.save(session)


async def _give_up(entry_id: uuid.UUID, error: BaseException) -> None:
    """Mark the entry failed, so it isn't tried again, and tell its owner (T-033)."""
    logger.warning("gave up on journal entry %s: %s", entry_id, type(error).__name__)
    async with database() as session:
        entry = await session.get(JournalEntry, entry_id, with_for_update=True)
        if entry is None or entry.processed_at is not None:
            return
        entry.failed_at = datetime.now(UTC)
        chat_id = await chat_of(session, entry.user_id)
    if chat_id is None:
        return
    notice = failure_text(_reason(error), entry_link(get_settings().app_url, entry_id))
    try:
        await _send(chat_id, notice)
    except TelegramError:
        logger.warning("couldn't tell the owner that journal entry %s failed", entry_id)


def _reason(error: BaseException) -> str:
    if isinstance(error, LLMRefusal):
        return "the model declined it"
    if isinstance(error, LLMInvalidOutput):
        return "the model's answer wasn't usable"
    if isinstance(error, LLMConfigError):
        return "the model settings are missing"
    return "of an error that kept coming back"


async def _send(chat_id: int, text: str) -> None:
    token = get_settings().telegram_bot_token
    if token is None:
        logger.warning("TELEGRAM_BOT_TOKEN isn't set, so the bot can't reply")
        return
    async with _telegram_http() as http:
        await BotApi(token, http).send_message(chat_id, text)


def _llm_client(log: CallLog) -> LLMClient:
    """The Claude client for one job, logging its calls to `log` (tests replace it with a fake)."""
    return LLMClient.from_settings(log=log)


def _telegram_http() -> httpx2.AsyncClient:
    """The HTTP client for Telegram's Bot API (tests replace it with a stand-in for Telegram)."""
    return httpx2.AsyncClient()


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
