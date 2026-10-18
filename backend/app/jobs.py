"""Background jobs and scheduled tasks (NFR-REL-1), on Procrastinate and PostgreSQL.

Jobs run in the worker (`wj worker`), outside web requests:
- `@jobs.task(retry=RETRY)` retries a job that raises, with exponential backoff;
- `@jobs.periodic(cron="...")` runs a task on a schedule;
- `@jobs.task(queue=LATEX_QUEUE)` is for a job that needs LaTeX: only a worker on a computer with
  TeX Live takes it (`worker_queues`), so on Vercel it waits for one (C6).

Job arguments are stored as plain JSON and appear in the worker's logs, so pass IDs, never journal
or fact text. Procrastinate's tables come from migration 0002; a newer Procrastinate needs a
migration that applies its new SQL files.
"""

import asyncio
import logging
import shutil
import uuid
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import procrastinate
from procrastinate.exceptions import AlreadyEnqueued
from procrastinate.jobs import DEFAULT_QUEUE
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catch_up import next_question
from app.config import get_settings
from app.db.engine import create_engine, session_factory, with_query
from app.db.models import CatchUp, JournalEntry
from app.llm.client import LLMClient, LLMConfigError, LLMInvalidOutput, LLMRefusal
from app.llm.usage import CallLog, DatabaseCallLog
from app.refresh import (
    RefreshInterrupted,
    begin_proposals,
    due_outputs,
    fail_refresh,
    outputs_to_update,
    record_outputs,
    record_proposals,
    render_master,
    running_refreshes,
)
from app.render.compile import CompileError
from app.render.fit import FitError
from app.render.resume import RenderError
from app.synthesis import Synthesis, synthesize
from app.telegram import catch_up
from app.telegram.api import BotApi, BotCall, TelegramError
from app.telegram.entries import close_quiet_entries
from app.telegram.follow_up import follow_up, record_follow_up
from app.telegram.journal import purge_updates
from app.telegram.processing import chat_of, entry_link, failure_text, process_entry
from app.telegram.refresh import failure_text as refresh_failure_text
from app.telegram.refresh import proposals_text
from app.telegram.roles import record_question, role_question

logger = logging.getLogger(__name__)

RETRY = procrastinate.RetryStrategy(max_attempts=5, exponential_wait=4)
"""Up to 5 retries after a failure, 4 s, 16 s, 64 s, 256 s and 1,024 s later (23 minutes in all)."""
UPDATE_RETENTION = timedelta(days=7)
"""How long raw Telegram updates are kept (FR-JRN-2)."""
LATEX_QUEUE = "latex"
"""The queue of the jobs that render resumes, which need LaTeX. The other jobs and the scheduled
tasks use Procrastinate's default queue."""


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


def worker_queues() -> list[str]:
    """The queues a worker here takes jobs from: `LATEX_QUEUE` too only where LaTeX is
    installed. Elsewhere, such as on Vercel, those jobs wait for a worker that has it (C6)."""
    if shutil.which("latexmk") is None:
        return [DEFAULT_QUEUE]
    return [DEFAULT_QUEUE, LATEX_QUEUE]


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
    now = datetime.fromtimestamp(timestamp, UTC)
    async with database() as session:
        closed = await close_quiet_entries(session, now, timeout)
    async with database() as session:
        queued = await queue_extractions(session, now=now, timeout=timeout)
    logger.info("closed %d quiet journal entries; queued %d for extraction", len(closed), queued)


@jobs.task(name="extract_entry", retry=RETRY, pass_context=True)
async def extract_entry(context: procrastinate.JobContext, entry_id: str) -> None:
    """Ask a follow-up question about a closed journal entry, if one is worth asking (FR-CAP-6),
    then which role it's about (FR-CAP-11). Once it has its role, triage it, save its facts and
    reply to its owner (FR-CAP-5). The owner is told when it finally fails."""
    job = context.job
    await run_extraction(
        uuid.UUID(entry_id),
        last_attempt=lambda error: RETRY.get_retry_decision(exception=error, job=job) is None,
    )


async def queue_extractions(session: AsyncSession, *, now: datetime, timeout: timedelta) -> int:
    """Queue `extract_entry` for each closed entry that's neither processed nor failed, unless
    it's queued already or waits for the owner: to choose its role, or to answer its follow-up
    question, which lapses after `timeout`. Jobs for one entry run one at a time. Returns how many
    were queued."""
    pending = await session.scalars(
        select(JournalEntry.id)
        .where(
            JournalEntry.closed_at.is_not(None),
            JournalEntry.processed_at.is_(None),
            JournalEntry.failed_at.is_(None),
            or_(
                JournalEntry.role_asked_at.is_(None),
                JournalEntry.role_id.is_not(None),
                JournalEntry.new_role.is_not(None),
            ),
            or_(
                JournalEntry.awaiting_follow_up.is_(False),
                JournalEntry.follow_up_at <= now - timeout,
            ),
        )
        .order_by(JournalEntry.closed_at)
    )
    return sum([await queue_extraction(entry_id) for entry_id in pending])


async def queue_extraction(entry_id: uuid.UUID) -> bool:
    """Queue `extract_entry` for one entry, unless it's queued already. Jobs for one entry run one
    at a time. Returns whether it was queued."""
    job = extract_entry.configure(
        lock=f"journal_entry:{entry_id}", queueing_lock=f"extract_entry:{entry_id}"
    )
    try:
        await job.defer_async(entry_id=str(entry_id))
    except AlreadyEnqueued:
        return False
    return True


async def run_extraction(
    entry_id: uuid.UUID, *, last_attempt: Callable[[BaseException], bool]
) -> None:
    """Ask the entry's follow-up question, if it's time to (`app/telegram/follow_up.py`), or else
    which role it's about, if that hasn't been asked (`app/telegram/roles.py`), or else process it
    (`app/telegram/processing.py`). A question or the reply is sent before the result is
    committed. A refusal or an unusable answer won't change on a retry, so it fails at once;
    anything else is retried, and fails when `last_attempt` says this was the last try."""
    async with database() as session:
        user_id = await session.scalar(
            select(JournalEntry.user_id).where(JournalEntry.id == entry_id)
        )
    if user_id is None:
        return
    log = DatabaseCallLog(user_id)
    timeout = timedelta(minutes=get_settings().entry_timeout_minutes)
    try:
        client = _llm_client(log)
        async with database() as session:
            asked = await follow_up(
                session, entry_id, client, now=datetime.now(UTC), timeout=timeout
            )
            if asked is not None:
                message_id = await _send(asked.chat_id, asked.text, **asked.options)
                await record_follow_up(session, entry_id, message_id)
                return
            question = await role_question(session, entry_id, now=datetime.now(UTC))
            if question is not None:
                if question.chat_id is not None:
                    message_id = await _send(question.chat_id, question.text, **question.options)
                    if message_id is not None:
                        await record_question(session, entry_id, message_id)
                return
            processed = await process_entry(
                session,
                entry_id,
                client,
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


@jobs.periodic(cron="* * * * *")
@jobs.task(name="queue_catch_up_questions", retry=RETRY)
async def queue_catch_up_questions(timestamp: int) -> None:
    """Queue `catch_up_question` for each catch-up interview that waits for its next question,
    such as one the bot couldn't ask at once. Due every minute, so it runs on every tick, and
    every minute under `wj worker`."""
    async with database() as session:
        thinking = await session.scalars(select(CatchUp.id).where(CatchUp.status == "thinking"))
        pending = list(thinking)
    queued = sum([await queue_catch_up(catch_up_id) for catch_up_id in pending])
    logger.info("queued %d catch-up questions", queued)


@jobs.task(name="catch_up_question", retry=RETRY, pass_context=True)
async def catch_up_question(context: procrastinate.JobContext, catch_up_id: str) -> None:
    """Write and send the catch-up interview's next question, or its end (FR-REM-4). The owner
    is told when it finally fails, and the interview pauses."""
    job = context.job
    await run_catch_up(
        uuid.UUID(catch_up_id),
        last_attempt=lambda error: RETRY.get_retry_decision(exception=error, job=job) is None,
    )


async def queue_catch_up(catch_up_id: uuid.UUID) -> bool:
    """Queue `catch_up_question` for one interview, unless it's queued or running already: a
    second copy queued while one runs would clash with that one's retry. Returns whether it was
    queued."""
    lock = f"catch_up:{catch_up_id}"
    if list(await jobs.job_manager.list_jobs_async(lock=lock, status="doing")):
        return False
    job = catch_up_question.configure(lock=lock, queueing_lock=f"catch_up_question:{catch_up_id}")
    try:
        await job.defer_async(catch_up_id=str(catch_up_id))
    except AlreadyEnqueued:
        return False
    return True


async def run_catch_up(
    catch_up_id: uuid.UUID, *, last_attempt: Callable[[BaseException], bool]
) -> None:
    """Write the interview's next question (`app/telegram/catch_up.py`) with no transaction
    open, so that the owner's messages don't wait for the model, and keep it. Then send it, and
    mark it asked. A question kept but not sent, such as when Telegram failed, is sent again on
    the retry without writing a new one, and so is the interview's last message, after which it
    finishes. A question that no longer waits once it's sent, such as one the owner paused
    while it was on its way, loses its buttons. Missing model settings won't change on a retry,
    so they fail at once; anything else is retried, and fails when `last_attempt` says this was
    the last try. A failed call to the model gives way to a fixed question (`app/catch_up.py`)."""
    async with database() as session:
        user_id = await session.scalar(select(CatchUp.user_id).where(CatchUp.id == catch_up_id))
    if user_id is None:
        return
    log = DatabaseCallLog(user_id)
    try:
        async with database() as session:
            planned = await catch_up.plan(session, catch_up_id)
        if planned is None:
            return
        question = planned.question
        if question is None:
            client = _llm_client(log)
            question = await asyncio.to_thread(
                next_question, planned.profile, planned.turns, client
            )
        async with database() as session:
            message = await catch_up.store(
                session, catch_up_id, planned, question, now=datetime.now(UTC)
            )
        if message is None:
            return
        message_id = await _send(message.chat_id, message.text, **message.options)
        async with database() as session:
            if not message.waits:
                await catch_up.finish(session, catch_up_id, planned, now=datetime.now(UTC))
                return
            waits = await catch_up.record_question(
                session, catch_up_id, message.text, message_id, now=datetime.now(UTC)
            )
        if not waits and message_id is not None:
            await _remove_buttons(message.chat_id, message_id)
    except LLMConfigError as error:
        await _catch_up_failed(catch_up_id, error)
    except Exception as error:
        if last_attempt(error):
            await _catch_up_failed(catch_up_id, error)
        raise
    finally:
        async with database() as session:
            await log.save(session)


async def _catch_up_failed(catch_up_id: uuid.UUID, error: BaseException) -> None:
    """Pause the interview, so it isn't tried again by itself, and tell its owner."""
    logger.warning("catch-up %s failed: %s", catch_up_id, type(error).__name__)
    async with database() as session:
        user_id = await catch_up.fail(session, catch_up_id)
        chat_id = None if user_id is None else await chat_of(session, user_id)
    if chat_id is None:
        return
    try:
        await _send(chat_id, catch_up.failure_text(_reason(error)))
    except TelegramError:
        logger.warning("couldn't tell the owner that catch-up %s failed", catch_up_id)


@jobs.periodic(cron="* * * * *")
@jobs.task(name="queue_refresh_jobs", retry=RETRY)
async def queue_refresh_jobs(timestamp: int) -> None:
    """Queue the work refreshes wait for: proposing changes, for each running refresh whose job
    isn't queued, such as one the bot couldn't run at once, and updating the outputs that are
    due (FR-REV-7). Due every minute, so it runs on every tick, and every minute under
    `wj worker`. Nothing is refreshed unless the owner asked (FR-REV-6)."""
    async with database() as session:
        running = await running_refreshes(session)
        due = await due_outputs(session)
    proposing = sum([await queue_refresh(refresh_id) for refresh_id in running])
    updating = sum([await queue_output_update(refresh_id) for refresh_id in due])
    logger.info("queued %d refreshes and %d output updates", proposing, updating)


@jobs.task(name="refresh_profile")
async def refresh_profile(refresh_id: str) -> None:
    """Propose profile changes from the facts since the last refresh, and tell the owner how
    many wait for review (FR-REV-6, FR-REM-6). It's never retried, since each try calls the
    heavy-tier model; the owner can send `/refresh` again."""
    await run_refresh(uuid.UUID(refresh_id))


async def queue_refresh(refresh_id: uuid.UUID) -> bool:
    """Queue `refresh_profile` for a running refresh, unless it's queued already. Returns
    whether it was queued."""
    job = refresh_profile.configure(
        lock=f"refresh:{refresh_id}", queueing_lock=f"refresh_profile:{refresh_id}"
    )
    try:
        await job.defer_async(refresh_id=str(refresh_id))
    except AlreadyEnqueued:
        return False
    return True


async def run_refresh(refresh_id: uuid.UUID) -> None:
    """Propose changes from the facts the refresh covers (`app/refresh.py`), outside a
    transaction, then save them and tell the owner. A failure fails the refresh and tells the
    owner, and so does running out of time, when the tick stops the job, or finding that an
    earlier try never finished. A message that can't be sent is only logged: the proposals are
    saved, and wait in the Inbox."""
    try:
        async with database() as session:
            inputs = await begin_proposals(session, refresh_id, now=datetime.now(UTC))
    except RefreshInterrupted:
        await _refresh_failed(refresh_id, "it didn't finish in time")
        return
    if inputs is None:
        return
    log = DatabaseCallLog(inputs.user_id)
    try:
        synthesis = Synthesis(change_set=None)
        if inputs.facts:
            synthesis = await asyncio.to_thread(
                synthesize,
                inputs.version.profile,
                inputs.facts,
                _llm_client(log),
                sources=inputs.sources,
                base_version=inputs.version.number,
                avoid=inputs.avoid,
            )
        async with database() as session:
            proposals = await record_proposals(session, inputs, synthesis, now=datetime.now(UTC))
            chat_id = await chat_of(session, inputs.user_id)
    except (LLMRefusal, LLMInvalidOutput, LLMConfigError) as error:
        await _refresh_failed(refresh_id, _reason(error))
        return
    except asyncio.CancelledError:
        await _refresh_failed(refresh_id, "it didn't finish in time")
        raise
    except Exception as error:
        await _refresh_failed(refresh_id, _reason(error, otherwise="of an error"))
        raise
    finally:
        async with database() as session:
            await log.save(session)
    if proposals is None or chat_id is None:
        return
    try:
        await _send(chat_id, proposals_text(proposals, get_settings().app_url))
    except TelegramError:
        logger.warning("couldn't tell the owner that refresh %s is ready", refresh_id)


async def _refresh_failed(refresh_id: uuid.UUID, reason: str) -> None:
    logger.warning("refresh %s failed: %s", refresh_id, reason)
    async with database() as session:
        user_id = await fail_refresh(session, refresh_id, now=datetime.now(UTC))
        chat_id = None if user_id is None else await chat_of(session, user_id)
    if chat_id is None:
        return
    try:
        await _send(chat_id, refresh_failure_text(reason))
    except TelegramError:
        logger.warning("couldn't tell the owner that refresh %s failed", refresh_id)


@jobs.task(name="update_outputs", queue=LATEX_QUEUE, retry=RETRY)
async def update_outputs(refresh_id: str) -> None:
    """Update a refresh's outputs from its version (FR-REV-7): render the master resume
    (FR-RES-5) and keep the PDF. Tailored resumes aren't touched."""
    await run_output_update(uuid.UUID(refresh_id))


async def queue_output_update(refresh_id: uuid.UUID) -> bool:
    """Queue `update_outputs` for a refresh, unless it's queued already. Returns whether it was
    queued."""
    job = update_outputs.configure(
        lock=f"outputs:{refresh_id}", queueing_lock=f"update_outputs:{refresh_id}"
    )
    try:
        await job.defer_async(refresh_id=str(refresh_id))
    except AlreadyEnqueued:
        return False
    return True


async def run_output_update(refresh_id: uuid.UUID) -> None:
    """Render the master resume from the version the refresh's outputs are to show, outside a
    transaction, and keep it. A resume that can't be rendered won't render on a retry, so the
    outputs are marked failed; the next refresh tries again. Only the error's type is logged: a
    LaTeX error can quote the resume."""
    async with database() as session:
        job = await outputs_to_update(session, refresh_id)
    if job is None:
        return
    try:
        rendered = await asyncio.to_thread(render_master, job.profile)
    except (CompileError, RenderError, FitError) as error:
        logger.warning(
            "couldn't render the master resume for refresh %s: %s",
            refresh_id,
            type(error).__name__,
        )
        rendered = None
    async with database() as session:
        kept = await record_outputs(session, job, rendered, now=datetime.now(UTC))
    if kept and rendered is not None:
        logger.info("the master resume shows profile version %d now", job.version)


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


def _reason(error: BaseException, *, otherwise: str = "of an error that kept coming back") -> str:
    if isinstance(error, LLMRefusal):
        return "the model declined it"
    if isinstance(error, LLMInvalidOutput):
        return "the model's answer wasn't usable"
    if isinstance(error, LLMConfigError):
        return "the model settings are missing"
    return otherwise


async def _send(chat_id: int, text: str, **options: Any) -> int | None:
    """Send a message, and return its ID, or None when the bot can't send one."""
    token = get_settings().telegram_bot_token
    if token is None:
        logger.warning("TELEGRAM_BOT_TOKEN isn't set, so the bot can't reply")
        return None
    async with _telegram_http() as http:
        return await BotApi(token, http).send_message(chat_id, text, **options)


async def _remove_buttons(chat_id: int, message_id: int) -> None:
    """Remove the buttons under one of the bot's messages. A failure is only logged."""
    token = get_settings().telegram_bot_token
    if token is None:
        return
    call = BotCall("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id})
    async with _telegram_http() as http:
        try:
            await BotApi(token, http).make(call)
        except TelegramError:
            logger.warning("couldn't remove the buttons under message %s", message_id)


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
    queues = worker_queues()
    if LATEX_QUEUE not in queues:
        logger.info("LaTeX isn't installed, so this worker leaves resumes for one that has it")
    async with jobs.open_async():
        await jobs.run_worker_async(
            queues=queues, concurrency=concurrency, install_signal_handlers=True
        )
