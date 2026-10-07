"""Closed journal entries against the test database: triage, facts, the reply, and the
`extract_entry` job with its retries and its sweep (T-033)."""

import json
import random
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from procrastinate.testing import InMemoryConnector
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.jobs as job_module
from app.config import Settings, get_settings
from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import FactRow, JournalEntry, JournalMessage, LlmCallRow, TelegramLink, User
from app.db.store import list_facts
from app.extraction import ExtractionReply
from app.jobs import (
    close_quiet_entries_task,
    database,
    extract_entry,
    jobs,
    queue_extractions,
    run_extraction,
)
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, refusal, reply
from app.llm.usage import CallLog, MemoryCallLog
from app.telegram.api import TelegramError
from app.telegram.processing import Processed, failure_text, process_entry
from app.tick import extract_now
from app.triage import TriageReply

pytestmark = pytest.mark.anyio

NOTE = "finally got the nightly export down from ~50 min to 12!! batching the db writes did it."
FOLLOW_UP = "also wrote the runbook for it"
EXPORT = "Cut the nightly export from about 50 to 12 minutes by batching database writes."
RUNBOOK = "Wrote the runbook for the nightly export."
OPENED = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
APP_URL = "https://journal.example.com"
WORK = TriageReply(label="work")
OTHER = TriageReply(label="other")
EXTRACTED = ExtractionReply.model_validate(
    {
        "facts": [
            {
                "statement": EXPORT,
                "kind": "accomplishment",
                "metrics": [
                    {
                        "subject": "nightly export duration",
                        "kind": "change",
                        "numbers": [50, 12],
                        "unit": "minutes",
                        "qualifier": "approximately",
                    }
                ],
            },
            {"statement": RUNBOOK, "kind": "accomplishment"},
            {
                "statement": "Grew the export to 300 users.",
                "kind": "accomplishment",
                "metrics": [
                    {"subject": "users", "kind": "single", "numbers": [300], "unit": "users"}
                ],
            },
        ]
    }
)
"""Two facts the note backs, and one with a number it doesn't give, which is left out."""
_random = random.SystemRandom()


def expected_reply(entry_id: uuid.UUID) -> str:
    return (
        "Saved 2 facts from this entry:\n"
        f"- {EXPORT}\n"
        f"- {RUNBOOK}\n"
        "I left out 1 fact with numbers your note doesn't give.\n"
        "\n"
        "See them in your journal:\n"
        f"{APP_URL}/journal#{entry_id}"
    )


@pytest.fixture(autouse=True)
def key_ring() -> Iterator[None]:
    use_key_ring(KeyRing.parse(new_key("k1")))
    yield
    use_key_ring(None)


@pytest.fixture
def chat_id() -> int:
    return _random.randrange(10**9, 10**12)


def llm_settings() -> Settings:
    return Settings(llm_model_light="light-model", llm_model_standard="standard-model")


async def new_entry(
    session: AsyncSession,
    *texts: str,
    chat_id: int | None = None,
    closed: bool = True,
    **state: Any,
) -> tuple[uuid.UUID, uuid.UUID]:
    """A user, linked to `chat_id` if given, with one entry holding `texts`. Returns both IDs."""
    user = User()
    session.add(user)
    await session.flush()
    if chat_id is not None:
        session.add(TelegramLink(user_id=user.id, chat_id=chat_id))
    last = OPENED + timedelta(minutes=len(texts))
    entry = JournalEntry(user_id=user.id, opened_at=OPENED, last_message_at=last, **state)
    if closed:
        entry.closed_at, entry.closed_by = last, "done"
    session.add(entry)
    await session.flush()
    chat = chat_id or _random.randrange(10**9, 10**12)
    for number, text in enumerate(texts, start=1):
        session.add(
            JournalMessage(
                user_id=user.id,
                chat_id=chat,
                message_id=number,
                entry_id=entry.id,
                sender="owner",
                text=text,
                sent_at=OPENED + timedelta(minutes=number),
            )
        )
    await session.flush()
    return user.id, entry.id


# --- Processing an entry -------------------------------------------------------------------------


async def test_an_entry_about_work_saves_its_facts_and_lists_them(
    session: AsyncSession, chat_id: int
) -> None:
    user_id, entry_id = await new_entry(session, NOTE, FOLLOW_UP, chat_id=chat_id)
    fake = FakeMessages(reply(WORK), reply(EXTRACTED))
    client = LLMClient(llm_settings(), fake, MemoryCallLog())
    processed = await process_entry(session, entry_id, client, now=NOW, app_url=APP_URL)
    assert processed == Processed(chat_id, expected_reply(entry_id))
    triage_request, extraction_request = fake.requests
    assert triage_request["model"] == "light-model"
    assert triage_request["messages"][0]["content"] == f"<note>\n{NOTE}\n\n{FOLLOW_UP}\n</note>"
    assert extraction_request["model"] == "standard-model"
    assert "The note was written on 2026-10-04." in extraction_request["messages"][0]["content"]
    saved = await list_facts(session, user_id)
    assert [(fact.id, fact.statement, fact.entry_id) for fact in saved] == [
        (f"{entry_id}_1", EXPORT, str(entry_id)),
        (f"{entry_id}_2", RUNBOOK, str(entry_id)),
    ]
    entry = await session.get(JournalEntry, entry_id)
    assert entry is not None
    assert (entry.triage, entry.processed_at) == ("work", NOW)


async def test_an_entry_not_about_work_gives_no_facts(session: AsyncSession, chat_id: int) -> None:
    """FR-CAP-8: the light-tier triage alone; no extraction."""
    user_id, entry_id = await new_entry(session, "made dumplings tonight", chat_id=chat_id)
    fake = FakeMessages(reply(OTHER))
    client = LLMClient(llm_settings(), fake, MemoryCallLog())
    processed = await process_entry(session, entry_id, client, now=NOW, app_url=APP_URL)
    assert processed is not None
    assert processed.reply == "Noted. This doesn't look like work, so I saved no facts from it."
    assert len(fake.requests) == 1
    assert await list_facts(session, user_id) == []
    entry = await session.get(JournalEntry, entry_id)
    assert entry is not None
    assert entry.triage == "other"


@pytest.mark.parametrize(
    "state",
    [
        {"closed": False},
        {"triage": "work", "processed_at": OPENED},
        {"failed_at": OPENED},
    ],
    ids=["open", "processed", "failed"],
)
async def test_only_a_closed_entry_waiting_to_be_processed_is_processed(
    session: AsyncSession, state: dict[str, Any]
) -> None:
    _, entry_id = await new_entry(session, NOTE, **state)
    client = LLMClient(llm_settings(), FakeMessages(), MemoryCallLog())
    assert await process_entry(session, entry_id, client, now=NOW, app_url=APP_URL) is None


async def test_an_entry_with_no_text_is_set_aside_without_a_call(session: AsyncSession) -> None:
    _, entry_id = await new_entry(session)
    client = LLMClient(llm_settings(), FakeMessages(), MemoryCallLog())
    assert await process_entry(session, entry_id, client, now=NOW, app_url=APP_URL) is None
    entry = await session.get(JournalEntry, entry_id)
    assert entry is not None
    assert (entry.triage, entry.processed_at) == ("other", NOW)


# --- The extract_entry job -----------------------------------------------------------------------


@pytest.fixture
def job_settings(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Jobs read their settings as the worker does."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("APP_URL", APP_URL)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def claude(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeMessages]:
    """Script the replies the job's Claude client gets."""

    def script(*replies: Any) -> FakeMessages:
        fake = FakeMessages(*replies)

        def client(log: CallLog) -> LLMClient:
            return LLMClient(llm_settings(), fake, log)

        monkeypatch.setattr(job_module, "_llm_client", client)
        return fake

    return script


class Telegram:
    """A stand-in for the Bot API: it keeps each message sent, and fails the first `failures`."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.failures = 0

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        assert request.url.path.endswith("/sendMessage")
        if self.failures:
            self.failures -= 1
            return httpx2.Response(502, text="Bad Gateway")
        self.sent.append(json.loads(request.content))
        return httpx2.Response(200, json={"ok": True, "result": {"message_id": 1}})


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> Telegram:
    bot = Telegram()
    transport = httpx2.MockTransport(bot.handle)
    monkeypatch.setattr(
        job_module, "_telegram_http", lambda: httpx2.AsyncClient(transport=transport)
    )
    return bot


Committed = Callable[..., Awaitable[tuple[uuid.UUID, uuid.UUID]]]


@pytest.fixture
async def committed(job_settings: None) -> AsyncIterator[Committed]:
    """Make an entry as `new_entry` does, but committed, as jobs see it. The users made are
    deleted afterwards, with all their rows, so that later tests see no stray journal text."""
    users: list[uuid.UUID] = []

    async def make(*texts: str, **options: Any) -> tuple[uuid.UUID, uuid.UUID]:
        async with database() as session:
            user_id, entry_id = await new_entry(session, *texts, **options)
        users.append(user_id)
        return user_id, entry_id

    yield make
    async with database() as session:
        await session.execute(delete(User).where(User.id.in_(users)))


async def entry_state(entry_id: uuid.UUID) -> tuple[str | None, bool, bool, int]:
    """The entry's label, whether it's processed and failed, and how many facts it has."""
    async with database() as session:
        entry = await session.get(JournalEntry, entry_id)
        assert entry is not None
        facts = await session.scalar(select(func.count()).where(FactRow.entry_id == str(entry_id)))
        return entry.triage, entry.processed_at is not None, entry.failed_at is not None, facts or 0


def never(error: BaseException) -> bool:
    return False


def always(error: BaseException) -> bool:
    return True


async def test_the_job_replies_in_the_owners_chat_and_logs_its_calls(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    user_id, entry_id = await committed(NOTE, FOLLOW_UP, chat_id=chat_id)
    claude(reply(WORK), reply(EXTRACTED))
    await run_extraction(entry_id, last_attempt=never)
    assert telegram.sent == [{"chat_id": chat_id, "text": expected_reply(entry_id)}]
    assert await entry_state(entry_id) == ("work", True, False, 2)
    async with database() as session:
        calls = await session.scalars(
            select(LlmCallRow.task).where(LlmCallRow.user_id == user_id).order_by(LlmCallRow.at)
        )
        assert list(calls) == ["message_triage", "fact_extraction"]


async def test_a_refusal_fails_the_entry_at_once_and_tells_the_owner(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    """A retry would get the same answer, so there's none."""
    _, entry_id = await committed(NOTE, chat_id=chat_id)
    claude(refusal())
    await run_extraction(entry_id, last_attempt=never)
    link = f"{APP_URL}/journal#{entry_id}"
    assert telegram.sent == [
        {"chat_id": chat_id, "text": failure_text("the model declined it", link)}
    ]
    assert await entry_state(entry_id) == (None, False, True, 0)


async def test_a_reply_that_cant_be_sent_undoes_the_attempt(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    """The facts and the processed mark are saved only once the reply is sent, so a retry starts
    from the beginning. Until the last attempt, the owner isn't told."""
    _, entry_id = await committed(NOTE, FOLLOW_UP, chat_id=chat_id)
    claude(reply(WORK), reply(EXTRACTED))
    telegram.failures = 1
    with pytest.raises(TelegramError):
        await run_extraction(entry_id, last_attempt=never)
    assert await entry_state(entry_id) == (None, False, False, 0)
    assert telegram.sent == []

    claude(reply(WORK), reply(EXTRACTED))
    await run_extraction(entry_id, last_attempt=never)
    assert telegram.sent == [{"chat_id": chat_id, "text": expected_reply(entry_id)}]
    assert await entry_state(entry_id) == ("work", True, False, 2)


async def test_the_last_failed_attempt_fails_the_entry_and_tells_the_owner(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    _, entry_id = await committed(NOTE, chat_id=chat_id)
    claude(reply(WORK), reply(EXTRACTED))
    telegram.failures = 1
    with pytest.raises(TelegramError):
        await run_extraction(entry_id, last_attempt=always)
    link = f"{APP_URL}/journal#{entry_id}"
    notice = failure_text("of an error that kept coming back", link)
    assert telegram.sent == [{"chat_id": chat_id, "text": notice}]
    assert await entry_state(entry_id) == (None, False, True, 0)


async def test_an_unusable_answer_fails_the_entry_at_once(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    _, entry_id = await committed(NOTE, chat_id=chat_id)
    claude(reply('{"label": "maybe"}'), reply('{"label": "unsure"}'))
    await run_extraction(entry_id, last_attempt=never)
    assert [message["text"].split(",")[0] for message in telegram.sent] == [
        "I couldn't read this entry because the model's answer wasn't usable"
    ]
    assert await entry_state(entry_id) == (None, False, True, 0)


async def test_the_worker_runs_the_job(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    """End to end through Procrastinate, on a queue of its own."""
    _, entry_id = await committed("made dumplings tonight", chat_id=chat_id)
    claude(reply(OTHER))
    async with jobs.open_async():
        job = extract_entry.configure(queue="extract-entry-test")
        job_id = await job.defer_async(entry_id=str(entry_id))
        await jobs.run_worker_async(
            queues=["extract-entry-test"], wait=False, install_signal_handlers=False
        )
        [done] = await jobs.job_manager.list_jobs_async(id=job_id)
    assert done.status == "succeeded"
    assert [message["chat_id"] for message in telegram.sent] == [chat_id]
    assert await entry_state(entry_id) == ("other", True, False, 0)


async def test_a_just_closed_entry_is_extracted_right_away(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    """What the webhook runs after answering an update that closed an entry (FR-CAP-5): a tick
    that first queues the entry, so the reply doesn't wait for the scheduler."""
    _, entry_id = await committed(NOTE, FOLLOW_UP, chat_id=chat_id)
    claude(reply(WORK), reply(EXTRACTED))
    with jobs.replace_connector(InMemoryConnector()):
        await extract_now(entry_id)
    assert telegram.sent == [{"chat_id": chat_id, "text": expected_reply(entry_id)}]
    assert await entry_state(entry_id) == ("work", True, False, 2)


# --- Queueing closed entries ---------------------------------------------------------------------


def queued(connector: InMemoryConnector) -> list[dict[str, Any]]:
    return [job for job in connector.jobs.values() if job["task_name"] == "extract_entry"]


async def test_closed_entries_are_queued_once_each(committed: Committed) -> None:
    _, waiting = await committed(NOTE)
    _, still_open = await committed(NOTE, closed=False)
    _, processed = await committed(NOTE, triage="work", processed_at=OPENED)
    _, failed = await committed(NOTE, failed_at=OPENED)
    connector = InMemoryConnector()
    with jobs.replace_connector(connector):
        async with database() as session:
            await queue_extractions(session)
            await queue_extractions(session)
    entries = [job["args"]["entry_id"] for job in queued(connector)]
    assert entries.count(str(waiting)) == 1
    assert {str(still_open), str(processed), str(failed)}.isdisjoint(entries)
    [job] = [job for job in queued(connector) if job["args"]["entry_id"] == str(waiting)]
    assert (job["lock"], job["queueing_lock"]) == (
        f"journal_entry:{waiting}",
        f"extract_entry:{waiting}",
    )


async def test_the_scheduled_task_queues_the_entries_it_closes(committed: Committed) -> None:
    """The tick closes a quiet entry and queues it in the same run (FR-CAP-5)."""
    _, quiet = await committed(NOTE, closed=False)
    connector = InMemoryConnector()
    later = OPENED + timedelta(hours=1)
    with jobs.replace_connector(connector):
        await close_quiet_entries_task(timestamp=int(later.timestamp()))
    assert str(quiet) in [job["args"]["entry_id"] for job in queued(connector)]
    async with database() as session:
        entry = await session.get(JournalEntry, quiet)
        assert entry is not None
        assert entry.closed_by == "quiet"
