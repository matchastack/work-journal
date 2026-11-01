"""The Tailor page's jobs against the test database (T-045): parsing a draft's posting, tailoring
it, rendering its resume, and the sweep that queues them. Claude is the fake client; LaTeX is a
stand-in that returns a fixture PDF."""

import hashlib
import logging
import random
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from procrastinate.testing import InMemoryConnector
from pydantic import TypeAdapter
from sqlalchemy import delete, select, update

import app.jobs as job_module
import app.render.resume as resume_module
from app.config import Settings, get_settings
from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import LlmCallRow, TailorDraftRow, User
from app.db.profiles import save_version
from app.db.store import find_render, save_facts
from app.jobs import (
    database,
    jobs,
    queue_tailor_jobs,
    run_draft_render,
    run_posting_parse,
    run_tailoring,
)
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, refusal, reply
from app.llm.usage import CallLog
from app.render.compile import CompiledPdf, CompileError, inspect_pdf
from app.schema.fact import Fact
from app.schema.jobs import JobPosting
from app.schema.profile import Profile
from app.tailor.drafts import create_draft, draft_to_render, record_render
from app.tailoring.tailor import PlannedBullet, PlannedItem, PlannedSkillLine, TailoringPlan
from app.tick import draft_now
from app.validate.claims import ClaimReply, JudgedClaim
from app.writing import WrittenBullet

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parents[1] / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
FACTS = TypeAdapter(tuple[Fact, ...]).validate_json(
    (FIXTURES / "facts.json").read_text(encoding="utf-8")
)
POSTING = JobPosting.model_validate_json(
    (FIXTURES / "postings" / "backend.json").read_text(encoding="utf-8")
)
POSTING_TEXT = (FIXTURES / "postings" / "backend.txt").read_text(encoding="utf-8")
PDF = (FIXTURES / "pdfs" / "backend-resume.pdf").read_bytes()
REPHRASED = "Cut the nightly export job from 50 to 12 minutes by batching PostgreSQL writes."
PLAN = TailoringPlan(
    role_type="backend",
    work=(
        PlannedItem(
            id="northwind",
            bullets=(PlannedBullet(id="nw_export", relevance="high", rephrase=True),),
        ),
    ),
    skills=(PlannedSkillLine(label="Languages", skills=("Python", "Kotlin")),),
)
SUPPORTED = ClaimReply(
    claims=(
        JudgedClaim(
            claim="Cut the nightly export job",
            reason="The fact says so.",
            verdict="supported",
            fact_ids=("fact_nw_export",),
        ),
    )
)
_random = random.SystemRandom()


def tailoring_replies() -> list[Any]:
    """The heavy-tier plan, then the new phrasing as written and judged."""
    return [reply(PLAN), reply(WrittenBullet(text=REPHRASED)), reply(SUPPORTED)]


@pytest.fixture
def job_settings(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Jobs read DATABASE_URL from the settings, as the worker does."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    use_key_ring(KeyRing.parse(new_key("k1")))
    yield
    use_key_ring(None)
    get_settings.cache_clear()


@pytest.fixture
def claude(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeMessages]:
    """Script the replies the jobs' Claude client gets."""

    def script(*replies: Any) -> FakeMessages:
        fake = FakeMessages(*replies)
        settings = Settings(
            llm_model_heavy="heavy-model",
            llm_model_standard="standard-model",
            llm_model_light="light-model",
        )

        def client(log: CallLog) -> LLMClient:
            return LLMClient(settings, fake, log)

        monkeypatch.setattr(job_module, "_llm_client", client)
        return fake

    return script


@pytest.fixture
def latex(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stands in for LaTeX: each compile gives the fixture PDF. Returns the sources compiled."""
    sources: list[str] = []

    def compile_latex(source: str, **_: object) -> CompiledPdf:
        sources.append(source)
        return inspect_pdf(PDF)

    monkeypatch.setattr(resume_module, "compile_latex", compile_latex)
    return sources


Committed = Callable[[], Awaitable[tuple[uuid.UUID, uuid.UUID]]]


@pytest.fixture
async def committed(job_settings: None) -> AsyncIterator[Committed]:
    """A user with the fixture profile and facts, and a draft for the fixture posting, committed
    as jobs see them. Returns both IDs. The users are deleted afterwards, with all their rows."""
    users: list[uuid.UUID] = []

    async def make() -> tuple[uuid.UUID, uuid.UUID]:
        async with database() as session:
            user = User(github_id=_random.randrange(10**9, 10**12), github_login="casey-example")
            session.add(user)
            await session.flush()
            await save_version(session, user.id, PROFILE, author="owner")
            await save_facts(session, user.id, FACTS)
            draft_id = await create_draft(session, user.id, POSTING_TEXT)
        users.append(user.id)
        return user.id, draft_id

    yield make
    async with database() as session:
        await session.execute(delete(User).where(User.id.in_(users)))


async def row_of(draft_id: uuid.UUID) -> TailorDraftRow:
    async with database() as session:
        row = await session.get(TailorDraftRow, draft_id)
        assert row is not None
        return row


async def tasks(user_id: uuid.UUID) -> list[str]:
    async with database() as session:
        calls = await session.scalars(
            select(LlmCallRow.task).where(LlmCallRow.user_id == user_id).order_by(LlmCallRow.at)
        )
        return list(calls)


def never(error: BaseException) -> bool:
    return False


async def test_the_jobs_parse_tailor_and_render_a_draft(
    committed: Committed, claude: Callable[..., FakeMessages], latex: list[str]
) -> None:
    user_id, draft_id = await committed()
    fake = claude(reply(POSTING), *tailoring_replies())
    connector = InMemoryConnector()
    with jobs.replace_connector(connector):
        await run_posting_parse(draft_id, last_attempt=never)
        row = await row_of(draft_id)
        assert row.status == "tailoring"
        assert row.posting is not None and row.posting["posting"]["title"] == POSTING.title
        queued = [(job["task_name"], job["args"]) for job in connector.jobs.values()]
        assert queued == [("tailor_draft", {"draft_id": str(draft_id)})], "IDs only"

        await run_tailoring(draft_id)
        row = await row_of(draft_id)
        assert (row.status, row.profile_version, row.render_status) == ("ready", 1, "waiting")
        assert row.tailoring is not None
        assert row.tailoring["proposals"][0]["report"]["text"] == REPHRASED
        assert "statement" not in str(row.tailoring), "no fact text is kept"
        queued = [(job["task_name"], job["queue_name"]) for job in connector.jobs.values()]
        assert ("render_draft", "latex") in queued, "for a worker with LaTeX"

    assert [request["model"] for request in fake.requests] == [
        "light-model",
        "heavy-model",
        "standard-model",
        "standard-model",
    ]
    assert await tasks(user_id) == [
        "posting_parsing",
        "tailoring_selection",
        "bullet_writing",
        "claim_verification",
    ]

    await run_draft_render(draft_id)
    row = await row_of(draft_id)
    assert (row.render_status, row.pdf_sha256) == ("rendered", hashlib.sha256(PDF).hexdigest())
    assert row.render is not None and row.render["pages"] == 1
    assert {"coverage", "ats", "cuts", "warnings"} <= set(row.render)
    assert len(latex) == 1
    async with database() as session:
        assert row.render_key is not None
        assert await find_render(session, user_id, row.render_key) is not None, "kept for reuse"


async def test_a_refusal_fails_the_draft_and_says_why(
    committed: Committed, claude: Callable[..., FakeMessages]
) -> None:
    _, draft_id = await committed()
    claude(refusal(), refusal())
    await run_posting_parse(draft_id, last_attempt=never)
    row = await row_of(draft_id)
    assert (row.status, row.error) == (
        "failed",
        "The posting couldn't be read because the model declined it.",
    )


async def test_a_tailoring_refusal_fails_the_draft(
    committed: Committed, claude: Callable[..., FakeMessages]
) -> None:
    _, draft_id = await committed()
    claude(reply(POSTING), refusal(), refusal())
    with jobs.replace_connector(InMemoryConnector()):
        await run_posting_parse(draft_id, last_attempt=never)
        await run_tailoring(draft_id)
    row = await row_of(draft_id)
    assert (row.status, row.error) == (
        "failed",
        "The resume couldn't be tailored because the model declined it.",
    )


async def test_a_tailoring_that_never_finished_isnt_tried_again(
    committed: Committed, claude: Callable[..., FakeMessages]
) -> None:
    """Another try would call the heavy-tier model again. One that started recently is left to
    the try that started it."""
    _, draft_id = await committed()
    fake = claude()
    recently = datetime.now(UTC) - timedelta(minutes=5)
    async with database() as session:
        await session.execute(
            update(TailorDraftRow)
            .where(TailorDraftRow.id == draft_id)
            .values(
                status="tailoring",
                started_at=recently,
                posting={"posting": POSTING.model_dump(mode="json"), "dropped": []},
            )
        )
    await run_tailoring(draft_id)
    assert (await row_of(draft_id)).status == "tailoring"
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    async with database() as session:
        await session.execute(
            update(TailorDraftRow).where(TailorDraftRow.id == draft_id).values(started_at=long_ago)
        )
    await run_tailoring(draft_id)
    row = await row_of(draft_id)
    assert (row.status, row.error) == (
        "failed",
        "The resume couldn't be tailored: it didn't finish in time.",
    )
    assert fake.requests == []


async def test_a_render_that_fails_says_why_to_the_owner_only(
    committed: Committed,
    claude: Callable[..., FakeMessages],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, draft_id = await committed()
    claude(reply(POSTING), *tailoring_replies())

    def fail(source: str, **_: object) -> CompiledPdf:
        raise CompileError("LaTeX failed: ! Undefined control sequence", log=source)

    monkeypatch.setattr(resume_module, "compile_latex", fail)
    with jobs.replace_connector(InMemoryConnector()):
        await run_posting_parse(draft_id, last_attempt=never)
        await run_tailoring(draft_id)
    with caplog.at_level(logging.DEBUG):
        await run_draft_render(draft_id)
    row = await row_of(draft_id)
    assert (row.render_status, row.render_error) == (
        "failed",
        "LaTeX failed: ! Undefined control sequence.",
    )
    assert "Undefined control sequence" not in caplog.text
    assert "CompileError" in caplog.text


async def test_a_render_isnt_given_to_a_draft_whose_choices_changed(
    committed: Committed, claude: Callable[..., FakeMessages], latex: list[str]
) -> None:
    """Another render follows the new choices; this one is still kept, for reuse."""
    user_id, draft_id = await committed()
    claude(reply(POSTING), *tailoring_replies())
    with jobs.replace_connector(InMemoryConnector()):
        await run_posting_parse(draft_id, last_attempt=never)
        await run_tailoring(draft_id)
    async with database() as session:
        job = await draft_to_render(session, draft_id)
    assert job is not None
    rendered = resume_module.render_resume(job.selection)
    async with database() as session:
        await session.execute(
            update(TailorDraftRow)
            .where(TailorDraftRow.id == draft_id)
            .values(choices={"uses": {}, "approved": [], "skills": {"Kotlin": "dropped"}})
        )
    async with database() as session:
        assert not await record_render(session, job, rendered)
    row = await row_of(draft_id)
    assert (row.render_status, row.pdf_sha256) == ("waiting", None)
    async with database() as session:
        assert await find_render(session, user_id, rendered.key) is not None


async def test_the_sweep_queues_what_drafts_wait_for(
    committed: Committed, claude: Callable[..., FakeMessages]
) -> None:
    """In case the request that made a draft couldn't run its job at once."""
    _, parsing = await committed()
    _, rendering = await committed()
    claude(reply(POSTING), *tailoring_replies())
    with jobs.replace_connector(InMemoryConnector()):
        await run_posting_parse(rendering, last_attempt=never)
        await run_tailoring(rendering)
    connector = InMemoryConnector()
    with jobs.replace_connector(connector):
        await queue_tailor_jobs(int(datetime.now(UTC).timestamp()))
        await queue_tailor_jobs(int(datetime.now(UTC).timestamp()))
    queued = [(job["task_name"], job["queue_name"], job["args"]) for job in connector.jobs.values()]
    assert ("parse_draft_posting", "default", {"draft_id": str(parsing)}) in queued
    assert ("render_draft", "latex", {"draft_id": str(rendering)}) in queued
    assert len(queued) == len(set(map(str, queued))), "queued once"


async def test_the_page_runs_a_drafts_jobs_right_after_answering(
    committed: Committed, claude: Callable[..., FakeMessages]
) -> None:
    """`draft_now`, as the routes call it: a tick that parses the posting and tailors the
    resume, so they don't wait for the daily one. The render waits for a worker with LaTeX."""
    _, draft_id = await committed()
    claude(reply(POSTING), *tailoring_replies())
    with jobs.replace_connector(InMemoryConnector()):
        await draft_now(draft_id)
    row = await row_of(draft_id)
    assert (row.status, row.render_status) == ("ready", "waiting")
