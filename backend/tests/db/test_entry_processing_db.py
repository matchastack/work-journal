"""Closed journal entries against the test database: triage, facts, the reply, and the
`extract_entry` job with its retries and its sweep (T-033)."""

import json
import random
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from app.db.profiles import save_version
from app.db.settings import save_reminder_state
from app.db.store import list_facts
from app.extraction import ExtractionReply
from app.follow_up import FollowUpReply
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
from app.schema.profile import Profile
from app.schema.settings import Answering, ReminderState
from app.telegram.api import TelegramError
from app.telegram.processing import Processed, failure_text, process_entry
from app.telegram.roles import NO_ROLES, QUESTION
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
ROLE = "northwind"
"""A role in the fixture profile."""
PROFILE = Profile.model_validate_json(
    (Path(__file__).parents[1] / "fixtures" / "profile.json").read_text(encoding="utf-8")
)
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
    profile: Profile | None = None,
    **state: Any,
) -> tuple[uuid.UUID, uuid.UUID]:
    """A user, linked to `chat_id` if given and with `profile` loaded if given, with one entry
    holding `texts`. Returns both IDs. Unless `state` says otherwise, the owner has chosen the
    entry's role (FR-CAP-11)."""
    if "new_role" not in state:
        state.setdefault("role_id", ROLE)
    user = User()
    session.add(user)
    await session.flush()
    if profile is not None:
        await save_version(session, user.id, profile, author="owner")
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


async def test_every_fact_links_to_the_role_the_owner_chose(
    session: AsyncSession, chat_id: int
) -> None:
    """FR-CAP-11: the owner's choice, not the model's."""
    user_id, entry_id = await new_entry(
        session, NOTE, FOLLOW_UP, chat_id=chat_id, profile=PROFILE, role_id="northwind"
    )
    elsewhere = EXTRACTED.model_copy(
        update={
            "facts": tuple(
                fact.model_copy(update={"role_id": "contoso"}) for fact in EXTRACTED.facts
            )
        }
    )
    client = LLMClient(llm_settings(), FakeMessages(reply(WORK), reply(elsewhere)), MemoryCallLog())
    await process_entry(session, entry_id, client, now=NOW, app_url=APP_URL)
    saved = await list_facts(session, user_id, role_id="northwind")
    assert [(fact.statement, fact.proposed_role) for fact in saved] == [
        (EXPORT, None),
        (RUNBOOK, None),
    ]


async def test_a_new_roles_name_goes_with_every_fact(session: AsyncSession, chat_id: int) -> None:
    user_id, entry_id = await new_entry(
        session,
        NOTE,
        FOLLOW_UP,
        chat_id=chat_id,
        profile=PROFILE,
        new_role="Data Engineer, Fabrikam",
    )
    client = LLMClient(llm_settings(), FakeMessages(reply(WORK), reply(EXTRACTED)), MemoryCallLog())
    await process_entry(session, entry_id, client, now=NOW, app_url=APP_URL)
    saved = await list_facts(session, user_id)
    assert [(fact.role_id, fact.proposed_role) for fact in saved] == [
        (None, "Data Engineer, Fabrikam"),
        (None, "Data Engineer, Fabrikam"),
    ]


ANSWER_NOTE = "The order services handle about 40,000 events a day, more on sale days."
"""An answer to the fixture profile's open question, `q_nw_volume`."""
VOLUME = "The order services handle about 40,000 events a day."
VOLUME_REPLY = ExtractionReply.model_validate(
    {
        "facts": [
            {
                "statement": VOLUME,
                "kind": "accomplishment",
                "metrics": [
                    {
                        "subject": "order events a day",
                        "kind": "single",
                        "numbers": [40000],
                        "unit": "events",
                        "qualifier": "approximately",
                    }
                ],
            }
        ]
    }
)


async def test_an_answers_facts_link_to_its_open_question(
    session: AsyncSession, chat_id: int
) -> None:
    """FR-EXT-4: triage and extraction read the answer alone, as any note, and code links its
    facts to the question. The reply says the next /refresh can use them."""
    user_id, entry_id = await new_entry(
        session, ANSWER_NOTE, chat_id=chat_id, profile=PROFILE, open_question_id="q_nw_volume"
    )
    fake = FakeMessages(reply(WORK), reply(VOLUME_REPLY))
    client = LLMClient(llm_settings(), fake, MemoryCallLog())
    processed = await process_entry(session, entry_id, client, now=NOW, app_url=APP_URL)
    assert processed == Processed(
        chat_id,
        "Saved 1 fact from this entry:\n"
        f"- {VOLUME}\n"
        "It answers your open question, so your next /refresh can use it.\n"
        "\n"
        "See them in your journal:\n"
        f"{APP_URL}/journal#{entry_id}",
    )
    triage_request, extraction_request = fake.requests
    assert triage_request["messages"][0]["content"] == f"<note>\n{ANSWER_NOTE}\n</note>"
    assert extraction_request["messages"][0]["content"] == (
        f"The note was written on 2026-10-04.\n\n<note>\n{ANSWER_NOTE}\n</note>"
    )
    question = PROFILE.open_questions[0].question
    assert question not in json.dumps(fake.requests), "the question is never evidence"
    [saved] = await list_facts(session, user_id)
    assert (saved.statement, saved.role_id, saved.question_id) == (
        VOLUME,
        ROLE,
        "q_nw_volume",
    )


async def test_an_answer_to_a_question_gone_from_the_profile_links_to_none(
    session: AsyncSession, chat_id: int
) -> None:
    """Such as one the owner closed while the answer waited. Its facts are saved all the same."""
    user_id, entry_id = await new_entry(
        session, ANSWER_NOTE, chat_id=chat_id, profile=PROFILE, open_question_id="q_nw_latency"
    )
    client = LLMClient(
        llm_settings(), FakeMessages(reply(WORK), reply(VOLUME_REPLY)), MemoryCallLog()
    )
    processed = await process_entry(session, entry_id, client, now=NOW, app_url=APP_URL)
    assert processed is not None
    assert "open question" not in processed.reply
    [saved] = await list_facts(session, user_id)
    assert (saved.statement, saved.question_id) == (VOLUME, None)


async def test_an_entry_waiting_for_its_role_is_not_processed(session: AsyncSession) -> None:
    _, entry_id = await new_entry(session, NOTE, role_id=None, role_asked_at=OPENED)
    fake = FakeMessages()
    client = LLMClient(llm_settings(), fake, MemoryCallLog())
    assert await process_entry(session, entry_id, client, now=NOW, app_url=APP_URL) is None
    assert fake.requests == []
    entry = await session.get(JournalEntry, entry_id)
    assert entry is not None
    assert (entry.triage, entry.processed_at) == (None, None)


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


def question(chat_id: int, *roles: tuple[str, str]) -> dict[str, Any]:
    """The question about an entry whose first message is message 1 in `chat_id`, with a button
    for each `(label, role ID)`."""
    buttons = [[{"text": label, "callback_data": role_id}] for label, role_id in roles]
    buttons.append([{"text": "New role", "callback_data": "+new"}])
    return {
        "chat_id": chat_id,
        "text": QUESTION,
        "reply_markup": {"inline_keyboard": buttons},
        "reply_parameters": {"message_id": 1, "allow_sending_without_reply": True},
    }


async def test_the_job_asks_which_role_first_and_waits_for_the_answer(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    """FR-CAP-11: a button for each role in the latest profile version, and New role. Nothing is
    extracted until the owner answers; then the job processes the entry."""
    _, entry_id = await committed(NOTE, FOLLOW_UP, chat_id=chat_id, profile=PROFILE, role_id=None)
    fake = claude()
    await run_extraction(entry_id, last_attempt=never)
    await run_extraction(entry_id, last_attempt=never)
    assert telegram.sent == [
        question(
            chat_id,
            ("Engineer, Northwind Traders (2025-now)", "northwind"),
            ("Research Intern, Contoso Research (2024-2024)", "contoso"),
        )
    ], "asked once"
    assert fake.requests == []
    async with database() as session:
        entry = await session.get(JournalEntry, entry_id)
        assert entry is not None
        assert (entry.role_asked_at is not None, entry.role_question_id) == (True, 1)
        assert (entry.processed_at, entry.failed_at) == (None, None)
        entry.role_id = "northwind"

    claude(reply(WORK), reply(EXTRACTED))
    await run_extraction(entry_id, last_attempt=never)
    assert telegram.sent[1] == {"chat_id": chat_id, "text": expected_reply(entry_id)}
    assert await entry_state(entry_id) == ("work", True, False, 2)


async def test_without_a_profile_only_a_new_role_can_be_given(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    _, entry_id = await committed(NOTE, chat_id=chat_id, role_id=None)
    claude()
    await run_extraction(entry_id, last_attempt=never)
    [sent] = telegram.sent
    assert sent["text"] == NO_ROLES
    assert sent["reply_markup"] == {
        "inline_keyboard": [[{"text": "New role", "callback_data": "+new"}]]
    }


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


# --- The follow-up question ----------------------------------------------------------------------

ASKED = "How much faster did the nightly export get?"
ANSWER = "it went from ~50 min to 12"
ROLES = (
    ("Engineer, Northwind Traders (2025-now)", "northwind"),
    ("Research Intern, Contoso Research (2024-2024)", "contoso"),
)


def just_closed(**state: Any) -> dict[str, Any]:
    """`new_entry` options for an entry that closed a minute ago, waiting for its role."""
    closed_at = datetime.now(UTC) - timedelta(minutes=1)
    return {"closed": False, "closed_at": closed_at, "closed_by": "done", "role_id": None, **state}


def follow_up_question(chat_id: int, text: str = ASKED) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {"inline_keyboard": [[{"text": "Skip", "callback_data": "+skip"}]]},
        "reply_parameters": {"message_id": 1, "allow_sending_without_reply": True},
    }


async def follow_up_state(entry_id: uuid.UUID) -> tuple[bool, int | None, bool, bool]:
    """Whether the bot has decided about the follow-up question, the question's message, whether
    it waits for an answer, and whether the role question was asked."""
    async with database() as session:
        entry = await session.get(JournalEntry, entry_id)
        assert entry is not None
        return (
            entry.follow_up_at is not None,
            entry.follow_up_question_id,
            entry.awaiting_follow_up,
            entry.role_asked_at is not None,
        )


async def test_the_job_asks_one_follow_up_question_before_the_role(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    """FR-CAP-6: the light-tier check finds a gap, and the bot asks once, with a Skip button. The
    role question waits until the question is answered; extraction then reads the answer."""
    user_id, entry_id = await committed(NOTE, chat_id=chat_id, profile=PROFILE, **just_closed())
    fake = claude(reply(FollowUpReply(missing="metrics", question=ASKED)))
    await run_extraction(entry_id, last_attempt=never)
    await run_extraction(entry_id, last_attempt=never)
    assert telegram.sent == [follow_up_question(chat_id)], "asked once, and nothing else yet"
    assert fake.requests[0]["model"] == "light-model"
    assert fake.requests[0]["messages"][0]["content"] == f"<note>\n{NOTE}\n</note>"
    assert await follow_up_state(entry_id) == (True, 1, True, False)

    async with database() as session:
        entry = await session.get(JournalEntry, entry_id)
        assert entry is not None
        entry.awaiting_follow_up = False
        session.add(
            JournalMessage(
                user_id=user_id,
                chat_id=chat_id,
                message_id=60,
                entry_id=entry_id,
                sender="owner",
                text=ANSWER,
                sent_at=datetime.now(UTC),
            )
        )
    await run_extraction(entry_id, last_attempt=never)
    assert telegram.sent[1] == question(chat_id, *ROLES)
    assert await follow_up_state(entry_id) == (True, 1, False, True)

    async with database() as session:
        entry = await session.get(JournalEntry, entry_id)
        assert entry is not None
        entry.role_id = "northwind"
    fake = claude(reply(WORK), reply(EXTRACTED))
    await run_extraction(entry_id, last_attempt=never)
    assert fake.requests[0]["messages"][0]["content"] == f"<note>\n{NOTE}\n\n{ANSWER}\n</note>"
    assert await entry_state(entry_id) == ("work", True, False, 2)
    async with database() as session:
        calls = await session.scalars(
            select(LlmCallRow.task).where(LlmCallRow.user_id == user_id).order_by(LlmCallRow.at)
        )
        assert list(calls) == ["follow_up_question", "message_triage", "fact_extraction"]


async def test_a_note_that_says_enough_goes_straight_to_its_role(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    _, entry_id = await committed(NOTE, chat_id=chat_id, profile=PROFILE, **just_closed())
    claude(reply(FollowUpReply(missing=None)))
    await run_extraction(entry_id, last_attempt=never)
    assert telegram.sent == [question(chat_id, *ROLES)]
    assert await follow_up_state(entry_id) == (True, None, False, True)


async def test_a_refused_check_means_no_question_rather_than_a_failed_entry(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    _, entry_id = await committed(NOTE, chat_id=chat_id, profile=PROFILE, **just_closed())
    claude(refusal())
    await run_extraction(entry_id, last_attempt=never)
    assert telegram.sent == [question(chat_id, *ROLES)]
    assert await entry_state(entry_id) == (None, False, False, 0)


async def test_a_question_that_cant_be_sent_is_asked_again(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    """The decision is saved only once the question is sent, so a retry decides again."""
    _, entry_id = await committed(NOTE, chat_id=chat_id, profile=PROFILE, **just_closed())
    claude(reply(FollowUpReply(missing="metrics", question=ASKED)))
    telegram.failures = 1
    with pytest.raises(TelegramError):
        await run_extraction(entry_id, last_attempt=never)
    assert await follow_up_state(entry_id) == (False, None, False, False)

    claude(reply(FollowUpReply(missing="metrics", question=ASKED)))
    await run_extraction(entry_id, last_attempt=never)
    assert telegram.sent == [follow_up_question(chat_id)]


async def test_an_unanswered_question_lapses_after_the_timeout(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    """The entry then goes on to its role, without an answer."""
    asked = datetime.now(UTC) - timedelta(minutes=30)
    _, entry_id = await committed(
        NOTE,
        chat_id=chat_id,
        profile=PROFILE,
        **just_closed(follow_up_at=asked, follow_up_question_id=7, awaiting_follow_up=True),
    )
    claude()
    await run_extraction(entry_id, last_attempt=never)
    assert telegram.sent == [question(chat_id, *ROLES)]
    assert await follow_up_state(entry_id) == (True, 7, False, True)


@pytest.mark.parametrize(
    "case",
    ["closed long ago", "another entry open", "naming a role", "another question", "no chat"],
)
async def test_no_question_when_the_owner_may_not_be_there_to_answer(
    committed: Committed,
    chat_id: int,
    claude: Callable[..., FakeMessages],
    telegram: Telegram,
    case: str,
) -> None:
    """The bot asks only right after the entry closed, when the owner's next message can't be
    meant for anything else. It decides without calling the model, and goes on to the role."""
    state = just_closed()
    if case == "closed long ago":
        state["closed_at"] -= timedelta(minutes=30)
    user_id, entry_id = await committed(
        NOTE, chat_id=None if case == "no chat" else chat_id, profile=PROFILE, **state
    )
    async with database() as session:
        at = datetime.now(UTC)
        other = JournalEntry(user_id=user_id, opened_at=at, last_message_at=at)
        if case != "another entry open":
            other.closed_at, other.closed_by = at, "done"
        other.naming_role = case == "naming a role"
        if case == "another question":
            other.follow_up_at, other.awaiting_follow_up = at, True
        session.add(other)
    fake = claude()
    await run_extraction(entry_id, last_attempt=never)
    assert fake.requests == []
    assert await follow_up_state(entry_id) == (True, None, False, True)
    if case != "no chat":
        assert telegram.sent == [question(chat_id, *ROLES)]


@pytest.mark.parametrize("lapsed", [False, True], ids=["awaited", "lapsed"])
async def test_no_question_while_an_open_questions_answer_is_awaited(
    committed: Committed,
    chat_id: int,
    claude: Callable[..., FakeMessages],
    telegram: Telegram,
    lapsed: bool,
) -> None:
    """After "Answer the question" (FR-EXT-4), the owner's next message is the answer, so it
    can't answer a follow-up question too. A wait that lapsed doesn't stop one."""
    user_id, entry_id = await committed(NOTE, chat_id=chat_id, profile=PROFILE, **just_closed())
    pressed = datetime.now(UTC) - timedelta(minutes=30 if lapsed else 1)
    async with database() as session:
        answering = Answering(question_id="q_nw_volume", since=pressed)
        await save_reminder_state(session, user_id, ReminderState(answering=answering))
    if lapsed:
        claude(reply(FollowUpReply(missing="metrics", question=ASKED)))
        await run_extraction(entry_id, last_attempt=never)
        assert telegram.sent == [follow_up_question(chat_id)]
        return
    fake = claude()
    await run_extraction(entry_id, last_attempt=never)
    assert fake.requests == []
    assert telegram.sent == [question(chat_id, *ROLES)]
    assert await follow_up_state(entry_id) == (True, None, False, True)


async def test_another_entrys_lapsed_question_doesnt_stop_a_new_one(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    user_id, entry_id = await committed(NOTE, chat_id=chat_id, profile=PROFILE, **just_closed())
    async with database() as session:
        at = datetime.now(UTC) - timedelta(hours=2)
        other = JournalEntry(
            user_id=user_id,
            opened_at=at,
            last_message_at=at,
            closed_at=at,
            closed_by="done",
            follow_up_at=at,
            awaiting_follow_up=True,
        )
        session.add(other)
    claude(reply(FollowUpReply(missing="impact", question="Who uses the export now?")))
    await run_extraction(entry_id, last_attempt=never)
    assert telegram.sent == [follow_up_question(chat_id, "Who uses the export now?")]
    assert (await follow_up_state(entry_id))[2] is True
    assert (await follow_up_state(other.id))[2] is False


async def test_an_answer_to_an_open_question_gets_no_follow_up_question(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    """It was written to answer a question already (FR-EXT-4). One whose role code couldn't
    choose goes on to its role question, without a call to the model."""
    _, entry_id = await committed(
        ANSWER_NOTE,
        chat_id=chat_id,
        profile=PROFILE,
        **just_closed(open_question_id="q_nw_volume"),
    )
    fake = claude()
    await run_extraction(entry_id, last_attempt=never)
    assert fake.requests == []
    assert telegram.sent == [question(chat_id, *ROLES)]
    assert await follow_up_state(entry_id) == (False, None, False, True)


async def test_an_entry_already_asked_about_its_role_gets_no_question(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    """Such as one closed before the bot asked follow-up questions."""
    _, entry_id = await committed(
        NOTE, chat_id=chat_id, profile=PROFILE, **just_closed(role_asked_at=OPENED)
    )
    fake = claude()
    await run_extraction(entry_id, last_attempt=never)
    assert (fake.requests, telegram.sent) == ([], [])
    assert await follow_up_state(entry_id) == (False, None, False, True)


# --- Queueing closed entries ---------------------------------------------------------------------


def queued(connector: InMemoryConnector) -> list[dict[str, Any]]:
    return [job for job in connector.jobs.values() if job["task_name"] == "extract_entry"]


async def test_closed_entries_are_queued_once_each(committed: Committed) -> None:
    """Including one not yet asked about its role, and one whose follow-up question has lapsed,
    but not one that waits for an answer."""
    _, waiting = await committed(NOTE)
    _, to_ask = await committed(NOTE, role_id=None)
    _, answered = await committed(NOTE, role_asked_at=OPENED, new_role="Data Engineer")
    _, unanswered = await committed(NOTE, role_id=None, role_asked_at=OPENED)
    _, still_open = await committed(NOTE, closed=False)
    _, processed = await committed(NOTE, triage="work", processed_at=OPENED)
    _, failed = await committed(NOTE, failed_at=OPENED)
    asked = NOW - timedelta(minutes=10)
    _, follow_up = await committed(NOTE, role_id=None, follow_up_at=asked, awaiting_follow_up=True)
    lapsed = asked - timedelta(minutes=20, seconds=1)
    _, lapsed_follow_up = await committed(
        NOTE, role_id=None, follow_up_at=lapsed, awaiting_follow_up=True
    )
    connector = InMemoryConnector()
    with jobs.replace_connector(connector):
        async with database() as session:
            await queue_extractions(session, now=NOW, timeout=timedelta(minutes=30))
            await queue_extractions(session, now=NOW, timeout=timedelta(minutes=30))
    entries = [job["args"]["entry_id"] for job in queued(connector)]
    counts = [entries.count(str(entry)) for entry in (waiting, to_ask, answered, lapsed_follow_up)]
    assert counts == [1, 1, 1, 1]
    not_queued = (unanswered, still_open, processed, failed, follow_up)
    assert {str(entry) for entry in not_queued}.isdisjoint(entries)
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
