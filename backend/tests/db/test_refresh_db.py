"""Refreshes against the test database (T-038): starting one, the facts it covers, the changes it
proposes and what the bot says, the update of its outputs, and the jobs that run it."""

import asyncio
import hashlib
import json
import random
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from anthropic.types.beta import BetaMessage
from procrastinate.testing import InMemoryConnector
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import app.jobs as job_module
from app.config import Settings, get_settings
from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import FactRow, LlmCallRow, Refresh, Setting, TelegramLink, User
from app.db.profiles import decide_op, list_change_sets, propose_change_set, save_version
from app.db.settings import (
    get_linkedin_draft,
    get_linkedin_pack,
    save_confidential_terms,
    save_linkedin_done,
    save_linkedin_draft,
)
from app.db.store import (
    get_artifact,
    list_applications,
    save_application,
    save_facts,
    store_artifact,
)
from app.inbox.review import apply_change_set
from app.jobs import (
    database,
    jobs,
    queue_refresh_jobs,
    run_output_update,
    run_pack_update,
    run_refresh,
)
from app.linkedin import LinkedInPack, WrittenSection, mark_done, write_pack
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, refusal, reply
from app.llm.usage import CallLog, MemoryCallLog
from app.refresh import (
    UNFINISHED_AFTER,
    Blocked,
    OutputsJob,
    Proposals,
    RefreshInputs,
    RefreshInterrupted,
    begin_proposals,
    change_set_decided,
    due_packs,
    keep_pack_progress,
    outputs_to_update,
    pack_to_write,
    record_outputs,
    record_pack,
    record_proposals,
    start_refresh,
)
from app.schema.changes import AddSkill, ChangeSet, ReviewedOp
from app.schema.fact import Fact
from app.schema.jobs import Application, JobPosting
from app.schema.profile import Profile, Skill
from app.schema.settings import ConfidentialTerms
from app.synthesis import PlannedBullet, Synthesis, SynthesisPlan, synthesize
from app.telegram.refresh import NOTHING_NEW, failure_text, proposals_text
from app.tick import refresh_now, tick
from app.validate.claims import ClaimReply, JudgedClaim
from app.writing import WrittenBullet

pytestmark = pytest.mark.anyio

PROFILE = Profile.model_validate_json(
    (Path(__file__).parents[1] / "fixtures" / "profile.json").read_text(encoding="utf-8")
)
NOW = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
EARLIER = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
APP_URL = "https://journal.example.com"
ALERT = Fact(
    id="e1_1",
    statement="Added an alert that pages on-call when the nightly export fails.",
    date="2026-09",
    role_id="northwind",
)
RUNBOOK = Fact(
    id="e1_2",
    statement="Wrote the runbook for the nightly export.",
    date="2026-09",
    role_id="northwind",
)
IMPORTED = Fact(
    id="imp_1",
    statement="Built the order service.",
    role_id="northwind",
    origin="import",
)
BULLET = "Added an alert that pages on-call when the nightly export job fails."
_random = random.SystemRandom()


def synthesis_replies() -> list[BetaMessage]:
    """The heavy-tier plan, one bullet from ALERT, then the bullet as written and judged."""
    plan = SynthesisPlan(bullets=(PlannedBullet(under="northwind", fact_ids=(ALERT.id,)),))
    judged = JudgedClaim(claim="Added an alert", reason="As stated.", verdict="supported")
    return [
        reply(plan),
        reply(WrittenBullet(text=BULLET)),
        reply(ClaimReply(claims=(judged,))),
    ]


def llm_settings() -> Settings:
    return Settings(llm_model_heavy="heavy-model", llm_model_standard="standard-model")


def synthesized(inputs: RefreshInputs, *replies: BetaMessage) -> Synthesis:
    client = LLMClient(llm_settings(), FakeMessages(*replies), MemoryCallLog())
    return synthesize(
        inputs.version.profile,
        inputs.facts,
        client,
        sources=inputs.sources,
        base_version=inputs.version.number,
    )


@pytest.fixture(autouse=True)
def key_ring() -> Iterator[None]:
    use_key_ring(KeyRing.parse(new_key("k1")))
    yield
    use_key_ring(None)


async def new_user(
    session: AsyncSession,
    *facts: Fact,
    profile: Profile | None = PROFILE,
    chat_id: int | None = None,
) -> uuid.UUID:
    """A user with `profile` as version 1 and `facts`, linked to `chat_id` if given."""
    user = User()
    session.add(user)
    await session.flush()
    if profile is not None:
        await save_version(session, user.id, profile, author="owner")
    await save_facts(session, user.id, facts)
    if chat_id is not None:
        session.add(TelegramLink(user_id=user.id, chat_id=chat_id))
    await session.flush()
    return user.id


async def started(session: AsyncSession, user_id: uuid.UUID) -> RefreshInputs:
    refresh_id = await start_refresh(session, user_id)
    assert isinstance(refresh_id, uuid.UUID)
    inputs = await begin_proposals(session, refresh_id, now=NOW)
    assert inputs is not None
    return inputs


async def refresh_row(session: AsyncSession, refresh_id: uuid.UUID) -> Refresh:
    row = await session.get(Refresh, refresh_id, populate_existing=True)
    assert row is not None
    return row


async def refreshed_at(session: AsyncSession, user_id: uuid.UUID) -> dict[str, datetime | None]:
    rows = await session.execute(
        select(FactRow.id, FactRow.refreshed_at).where(FactRow.user_id == user_id)
    )
    return {fact_id: when for fact_id, when in rows}


SKILL = ChangeSet(
    author="ai", ops=(ReviewedOp(op=AddSkill(skill=Skill(name="Go", category="Languages"))),)
)
"""A change set with one change, which no refresh proposed."""


# --- Starting a refresh --------------------------------------------------------------------------


async def test_one_refresh_runs_at_a_time(session: AsyncSession) -> None:
    user_id = await new_user(session)
    first = await start_refresh(session, user_id)
    assert isinstance(first, uuid.UUID)
    assert await start_refresh(session, user_id) == Blocked("running", first)
    row = await refresh_row(session, first)
    assert (row.status, row.started_at, row.finished_at) == ("running", None, None)


async def test_a_refresh_needs_a_profile(session: AsyncSession) -> None:
    user_id = await new_user(session, ALERT, profile=None)
    assert await start_refresh(session, user_id) == Blocked("no_profile")


async def test_a_refresh_waits_until_proposed_changes_are_decided(session: AsyncSession) -> None:
    """New changes would be proposed against a profile the waiting ones may still change."""
    user_id = await new_user(session, ALERT)
    await propose_change_set(session, user_id, SKILL)
    assert await start_refresh(session, user_id) == Blocked("waiting")


# --- Proposing changes ---------------------------------------------------------------------------


async def test_a_refresh_covers_the_journal_facts_no_refresh_covered(
    session: AsyncSession,
) -> None:
    """FR-REV-6: new facts and edited ones; not imported facts or ones a refresh covered."""
    user_id = await new_user(session, ALERT, RUNBOOK, IMPORTED)
    await session.execute(
        update(FactRow)
        .where(FactRow.user_id == user_id, FactRow.id == RUNBOOK.id)
        .values(refreshed_at=EARLIER)
    )
    await save_confidential_terms(session, user_id, ConfidentialTerms(terms=("Falcon",)))
    inputs = await started(session, user_id)
    assert inputs.facts == (ALERT,)
    assert {fact.id for fact in inputs.sources} == {ALERT.id, RUNBOOK.id, IMPORTED.id}
    assert inputs.version.number == 1
    assert inputs.confidential == ("Falcon",), "the verifier checks each bullet for them"
    row = await refresh_row(session, inputs.refresh_id)
    assert row.started_at == NOW


async def test_a_try_leaves_a_refresh_another_started_recently(session: AsyncSession) -> None:
    """Such as `wj propose --db`, still at work; one that started long ago never finished."""
    user_id = await new_user(session, ALERT)
    inputs = await started(session, user_id)
    later = NOW + UNFINISHED_AFTER
    assert await begin_proposals(session, inputs.refresh_id, now=later) is None
    with pytest.raises(RefreshInterrupted):
        await begin_proposals(session, inputs.refresh_id, now=later + timedelta(seconds=1))


async def test_the_proposals_are_saved_and_the_facts_covered(session: AsyncSession) -> None:
    """A fact the proposals don't use is covered too: it waits in the brag bank (FR-REV-6)."""
    user_id = await new_user(session, ALERT, RUNBOOK)
    inputs = await started(session, user_id)
    synthesis = synthesized(inputs, *synthesis_replies())
    proposals = await record_proposals(session, inputs, synthesis, now=NOW)
    assert proposals == Proposals(user_id=user_id, facts=2, changes=1)
    row = await refresh_row(session, inputs.refresh_id)
    assert (row.status, row.finished_at, row.fact_count) == ("proposed", NOW, 2)
    assert (row.outputs, row.output_version) == (None, None), "not until they're decided"
    [saved] = await list_change_sets(session, user_id)
    assert row.change_set_id == saved.id
    assert [reviewed.op.fact_ids for reviewed in saved.change_set.ops] == [(ALERT.id,)]
    assert await refreshed_at(session, user_id) == {ALERT.id: NOW, RUNBOOK.id: NOW}
    assert await record_proposals(session, inputs, synthesis, now=NOW) is None, "once"


async def test_a_fact_edited_during_the_refresh_waits_for_the_next(
    session: AsyncSession,
) -> None:
    user_id = await new_user(session, ALERT, RUNBOOK)
    # Saved earlier, as in a transaction before this one.
    await session.execute(
        update(FactRow).where(FactRow.user_id == user_id).values(updated_at=EARLIER)
    )
    inputs = await started(session, user_id)
    edited = RUNBOOK.model_copy(update={"statement": "Wrote the runbook for both exports."})
    await save_facts(session, user_id, [edited])
    await record_proposals(session, inputs, synthesized(inputs, *synthesis_replies()), now=NOW)
    assert await refreshed_at(session, user_id) == {ALERT.id: NOW, RUNBOOK.id: None}

    # Once the change set is decided, the next refresh covers the edited fact.
    [saved] = await list_change_sets(session, user_id)
    await decide_op(session, user_id, saved.id, 0, "rejected")
    await apply_change_set(session, user_id, saved.id, now=NOW)
    later = await started(session, user_id)
    assert later.facts == (edited,)


async def test_nothing_new_brings_the_outputs_up_to_date(session: AsyncSession) -> None:
    """FR-REV-7: with no new facts, the outputs still move to the newest version, so the owner's
    own edits reach them. An earlier refresh's outputs that still wait are superseded."""
    user_id = await new_user(session, IMPORTED)
    first = await started(session, user_id)
    assert first.facts == ()
    proposals = await record_proposals(session, first, Synthesis(change_set=None), now=NOW)
    assert proposals == Proposals(user_id=user_id, facts=0, changes=0)
    row = await refresh_row(session, first.refresh_id)
    assert (row.status, row.change_set_id) == ("nothing_new", None)
    assert (row.outputs, row.output_version, row.outputs_at) == ("pending", 1, NOW)
    assert (row.linkedin_pack, row.linkedin_pack_at) == ("pending", NOW), "the pack too"

    edited = PROFILE.model_copy(update={"skill_gaps": ("Rust",)})
    await save_version(session, user_id, edited, author="owner")
    second = await started(session, user_id)
    later = NOW + timedelta(days=1)
    await record_proposals(session, second, Synthesis(change_set=None), now=later)
    assert ((await refresh_row(session, second.refresh_id)).output_version) == 2
    old = await refresh_row(session, first.refresh_id)
    assert (old.outputs, old.outputs_at) == ("superseded", later)
    assert (old.linkedin_pack, old.linkedin_pack_at) == ("superseded", later)
    packs = await due_packs(session)
    assert second.refresh_id in packs and first.refresh_id not in packs


@pytest.mark.parametrize(("decision", "version"), [("accepted", 2), ("rejected", 1)])
async def test_deciding_the_changes_makes_the_outputs_due(
    session: AsyncSession, decision: Any, version: int
) -> None:
    """FR-REV-7: applying the accepted changes updates the outputs from the new version. With
    every change rejected, they still move to the newest version."""
    user_id = await new_user(session, ALERT)
    inputs = await started(session, user_id)
    await record_proposals(session, inputs, synthesized(inputs, *synthesis_replies()), now=NOW)
    [saved] = await list_change_sets(session, user_id)
    await decide_op(session, user_id, saved.id, 0, decision)
    await apply_change_set(session, user_id, saved.id, now=NOW)
    row = await refresh_row(session, inputs.refresh_id)
    assert (row.outputs, row.output_version, row.linkedin_pack) == ("pending", version, "pending")


async def test_a_change_set_no_refresh_proposed_doesnt_touch_the_outputs(
    session: AsyncSession,
) -> None:
    """Owner edits reach the outputs at the next refresh (FR-REV-7)."""
    user_id = await new_user(session)
    change_set_id = await propose_change_set(session, user_id, SKILL)
    await change_set_decided(session, user_id, change_set_id, now=NOW)
    assert await session.scalar(select(Refresh.id).where(Refresh.user_id == user_id)) is None


# --- Updating the outputs ------------------------------------------------------------------------


async def due(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    """A refresh with nothing new, whose outputs wait to be updated."""
    inputs = await started(session, user_id)
    await record_proposals(session, inputs, Synthesis(change_set=None), now=NOW)
    return inputs.refresh_id


async def test_outputs_that_cant_be_rendered_are_marked_failed(session: AsyncSession) -> None:
    user_id = await new_user(session)
    refresh_id = await due(session, user_id)
    job = await outputs_to_update(session, refresh_id)
    assert job is not None
    assert job == OutputsJob(refresh_id=refresh_id, user_id=user_id, version=1, profile=PROFILE)
    assert await record_outputs(session, job, None, now=NOW)
    row = await refresh_row(session, refresh_id)
    assert (row.outputs, row.master_pdf_sha256) == ("failed", None)
    assert await outputs_to_update(session, refresh_id) is None


async def test_superseded_outputs_are_left_as_they_are(session: AsyncSession) -> None:
    user_id = await new_user(session)
    first = await due(session, user_id)
    job = await outputs_to_update(session, first)
    assert job is not None
    second = await due(session, user_id)
    assert not await record_outputs(session, job, None, now=NOW)
    assert (await refresh_row(session, first)).outputs == "superseded"
    assert (await refresh_row(session, second)).outputs == "pending"


# --- The jobs ------------------------------------------------------------------------------------


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
    """A stand-in for the Bot API that keeps each message sent."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        assert request.url.path.endswith("/sendMessage")
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


@pytest.fixture
def chat_id() -> int:
    return _random.randrange(10**9, 10**12)


Committed = Callable[..., Awaitable[tuple[uuid.UUID, uuid.UUID]]]


@pytest.fixture
async def committed(job_settings: None) -> AsyncIterator[Committed]:
    """A user made as `new_user` does, with a refresh started, committed as jobs see them.
    Returns both IDs. The users are deleted afterwards, with all their rows."""
    users: list[uuid.UUID] = []

    async def make(*facts: Fact, **options: Any) -> tuple[uuid.UUID, uuid.UUID]:
        async with database() as session:
            user_id = await new_user(session, *facts, **options)
            refresh_id = await start_refresh(session, user_id)
        users.append(user_id)
        assert isinstance(refresh_id, uuid.UUID)
        return user_id, refresh_id

    yield make
    async with database() as session:
        await session.execute(delete(User).where(User.id.in_(users)))


async def committed_row(refresh_id: uuid.UUID) -> Refresh:
    async with database() as session:
        row = await session.get(Refresh, refresh_id)
        assert row is not None
        return row


async def test_the_job_proposes_changes_and_tells_the_owner(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    """FR-REM-6: "N updates proposed", with a link to review them."""
    user_id, refresh_id = await committed(ALERT, RUNBOOK, chat_id=chat_id)
    fake = claude(*synthesis_replies())
    await run_refresh(refresh_id)
    text = f"1 update proposed from 2 facts since your last refresh. Review them:\n{APP_URL}/inbox"
    assert telegram.sent == [{"chat_id": chat_id, "text": text}]
    assert proposals_text(Proposals(user_id, facts=2, changes=1), APP_URL) == text
    assert fake.requests[0]["model"] == "heavy-model"
    row = await committed_row(refresh_id)
    assert (row.status, row.fact_count, row.change_set_id is not None) == ("proposed", 2, True)
    async with database() as session:
        calls = await session.scalars(
            select(LlmCallRow.task).where(LlmCallRow.user_id == user_id).order_by(LlmCallRow.at)
        )
        assert list(calls) == ["profile_synthesis", "bullet_writing", "claim_verification"]


async def test_the_bot_runs_the_refresh_right_after_answering(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    """`refresh_now`, as the webhook calls it after answering `/refresh`: a tick that runs the
    refresh's job, so the proposals don't wait for the daily one."""
    _, refresh_id = await committed(ALERT, chat_id=chat_id)
    claude(*synthesis_replies())
    with jobs.replace_connector(InMemoryConnector()):
        await refresh_now(refresh_id)
    assert [message["text"].split(".")[0] for message in telegram.sent] == [
        "1 update proposed from 1 fact since your last refresh"
    ]
    assert (await committed_row(refresh_id)).status == "proposed"


async def test_with_nothing_new_the_job_says_so_without_a_call(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    _, refresh_id = await committed(IMPORTED, chat_id=chat_id)
    fake = claude()
    await run_refresh(refresh_id)
    assert telegram.sent == [{"chat_id": chat_id, "text": NOTHING_NEW}]
    assert fake.requests == []
    row = await committed_row(refresh_id)
    assert (row.status, row.outputs, row.output_version) == ("nothing_new", "pending", 1)


def test_the_bot_says_when_new_facts_change_nothing() -> None:
    user_id = uuid.uuid4()
    assert proposals_text(Proposals(user_id, facts=1, changes=0), APP_URL) == (
        "Nothing to review: the 1 fact since your last refresh adds nothing to your profile."
    )
    assert proposals_text(Proposals(user_id, facts=3, changes=0), APP_URL) == (
        "Nothing to review: the 3 facts since your last refresh add nothing to your profile."
    )


async def test_a_refusal_fails_the_refresh_and_its_facts_wait(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    user_id, refresh_id = await committed(ALERT, chat_id=chat_id)
    claude(refusal(), refusal())
    await run_refresh(refresh_id)
    assert telegram.sent == [{"chat_id": chat_id, "text": failure_text("the model declined it")}]
    assert (await committed_row(refresh_id)).status == "failed"
    async with database() as session:
        assert await refreshed_at(session, user_id) == {ALERT.id: None}
        assert isinstance(await start_refresh(session, user_id), uuid.UUID), "can start again"


async def test_a_refresh_that_never_finished_isnt_tried_again(
    committed: Committed, chat_id: int, claude: Callable[..., FakeMessages], telegram: Telegram
) -> None:
    """Such as one whose function was stopped: another try would call the heavy-tier model
    again. One that started recently is left to the try that started it, such as
    `wj propose --db`."""
    _, refresh_id = await committed(ALERT, chat_id=chat_id)
    fake = claude()
    recently = datetime.now(UTC) - timedelta(minutes=5)
    async with database() as session:
        await session.execute(
            update(Refresh).where(Refresh.id == refresh_id).values(started_at=recently)
        )
    await run_refresh(refresh_id)
    assert (telegram.sent, (await committed_row(refresh_id)).status) == ([], "running")

    async with database() as session:
        await session.execute(
            update(Refresh).where(Refresh.id == refresh_id).values(started_at=NOW)
        )
    await run_refresh(refresh_id)
    assert telegram.sent == [{"chat_id": chat_id, "text": failure_text("it didn't finish in time")}]
    assert fake.requests == []
    assert (await committed_row(refresh_id)).status == "failed"


async def test_a_refresh_stopped_at_the_time_limit_fails_and_says_so(
    committed: Committed,
    chat_id: int,
    telegram: Telegram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tick stops a job that runs past its time; the owner hears at once, not at the next
    tick."""
    _, refresh_id = await committed(ALERT, chat_id=chat_id)
    planning = threading.Event()
    stop = threading.Event()

    def slow_synthesis(*args: Any, **kwargs: Any) -> Synthesis:
        planning.set()
        stop.wait(timeout=10)
        return Synthesis(change_set=None)

    monkeypatch.setattr(job_module, "synthesize", slow_synthesis)
    job = asyncio.create_task(run_refresh(refresh_id))
    try:
        while not planning.is_set():
            await asyncio.sleep(0.01)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
    finally:
        stop.set()
    assert telegram.sent == [{"chat_id": chat_id, "text": failure_text("it didn't finish in time")}]
    assert (await committed_row(refresh_id)).status == "failed"


async def test_the_sweep_queues_what_refreshes_wait_for(committed: Committed) -> None:
    """A running refresh's job, in case the bot couldn't run it at once, and the update of the
    outputs that are due: the master resume on the queue for workers with LaTeX, and the LinkedIn
    pack on the default queue."""
    _, running = await committed(ALERT)
    _, pending = await committed()
    async with database() as session:
        await session.execute(
            update(Refresh)
            .where(Refresh.id == pending)
            .values(
                status="nothing_new",
                finished_at=NOW,
                outputs="pending",
                output_version=1,
                linkedin_pack="pending",
            )
        )
    connector = InMemoryConnector()
    with jobs.replace_connector(connector):
        await queue_refresh_jobs(int(NOW.timestamp()))
        await queue_refresh_jobs(int(NOW.timestamp()))
    queued = [(job["task_name"], job["queue_name"], job["args"]) for job in connector.jobs.values()]
    assert ("refresh_profile", "default", {"refresh_id": str(running)}) in queued
    assert ("update_outputs", "latex", {"refresh_id": str(pending)}) in queued
    assert ("write_linkedin_pack", "default", {"refresh_id": str(pending)}) in queued, "no LaTeX"
    assert len(queued) == len(set(map(str, queued))), "queued once"


@pytest.mark.latex
async def test_the_output_update_renders_the_master_resume(committed: Committed) -> None:
    """FR-RES-5: the master resume from the refresh's version, kept as a PDF. A tailored resume
    made before stays as it was."""
    user_id, refresh_id = await committed()
    tailored = b"%PDF-1.4 tailored"
    application = Application(
        id="app_1",
        applied_on=date(2026, 10, 1),
        company="Fabrikam",
        position="Backend Engineer",
        posting=JobPosting(title="Backend Engineer", company="Fabrikam"),
        posting_text="We need a backend engineer.",
        title_variant="Software Engineer",
        pdf_sha256=hashlib.sha256(tailored).hexdigest(),
    )
    async with database() as session:
        await store_artifact(session, user_id, tailored, "application/pdf")
        await save_application(session, user_id, application)
        inputs = await begin_proposals(session, refresh_id, now=NOW)
        assert inputs is not None
        await record_proposals(session, inputs, Synthesis(change_set=None), now=NOW)

    await run_output_update(refresh_id)
    row = await committed_row(refresh_id)
    assert (row.outputs, row.output_version) == ("updated", 1)
    assert row.master_pages is not None and row.master_pages >= 1
    assert row.master_pdf_sha256 is not None
    async with database() as session:
        stored = await get_artifact(session, user_id, row.master_pdf_sha256)
        assert stored is not None
        assert stored[0] == "application/pdf" and stored[1].startswith(b"%PDF")
        assert await list_applications(session, user_id) == [application]
        assert await get_artifact(session, user_id, application.pdf_sha256 or "") == (
            "application/pdf",
            tailored,
        )
    await run_output_update(refresh_id)
    assert (await committed_row(refresh_id)).outputs_at == row.outputs_at, "once"


# --- The LinkedIn pack ---------------------------------------------------------------------------

PACK = {
    "headline": "Software Engineer | Python services and data tooling",
    "about": "I build Python services at Northwind Traders.\n\nBefore, I built research tools.",
    "position:northwind": "I build Python services that process order events.",
    "position:contoso": "I built a Python tool that labelled sensor logs.",
}
SUPPORTED = ClaimReply(
    claims=(JudgedClaim(claim="Python", reason="The facts say so.", verdict="supported"),)
)


def pack_replies(*keys: str) -> list[BetaMessage]:
    """For each section, the writer's reply and a claim check that passes it."""
    return [
        message
        for key in keys
        for message in (reply(WrittenSection(text=PACK[key])), reply(SUPPORTED))
    ]


def never_last(error: BaseException) -> bool:
    return False


async def test_a_pack_that_cant_be_written_is_marked_failed_apart_from_the_master_resume(
    session: AsyncSession,
) -> None:
    user_id = await new_user(session)
    refresh_id = await due(session, user_id)
    job = await pack_to_write(session, refresh_id)
    assert job is not None
    assert (job.user_id, job.version, job.profile, job.written) == (user_id, 1, PROFILE, ())
    assert await record_pack(session, job, None, now=NOW)
    row = await refresh_row(session, refresh_id)
    assert (row.linkedin_pack, row.linkedin_pack_at, row.outputs) == ("failed", NOW, "pending")
    assert await pack_to_write(session, refresh_id) is None
    assert await outputs_to_update(session, refresh_id) is not None, "the master resume waits"


async def test_a_superseded_pack_is_left_as_it_is(session: AsyncSession) -> None:
    user_id = await new_user(session)
    first = await due(session, user_id)
    job = await pack_to_write(session, first)
    assert job is not None
    second = await due(session, user_id)
    assert not await record_pack(session, job, LinkedInPack(sections=()), now=NOW)
    assert (await refresh_row(session, first)).linkedin_pack == "superseded"
    assert (await refresh_row(session, second)).linkedin_pack == "pending"
    assert await get_linkedin_pack(session, user_id) is None


async def test_sections_written_from_the_same_version_are_read_back(
    session: AsyncSession,
) -> None:
    """For a try that follows one stopped part way. Sections from another version aren't."""
    user_id = await new_user(session)
    refresh_id = await due(session, user_id)
    job = await pack_to_write(session, refresh_id)
    assert job is not None
    section = written_pack(job.profile).sections[0]
    await keep_pack_progress(session, job, [section])
    again = await pack_to_write(session, refresh_id)
    assert again is not None and again.written == (section,)
    await save_linkedin_draft(
        session, user_id, LinkedInPack(sections=(section,), profile_version=2)
    )
    other = await pack_to_write(session, refresh_id)
    assert other is not None and other.written == ()


def written_pack(profile: Profile) -> LinkedInPack:
    """The pack as the fake client writes it, with every section passing."""
    client = LLMClient(llm_settings(), FakeMessages(*pack_replies(*PACK)), MemoryCallLog())
    return write_pack(profile, client)


async def due_committed(committed: Committed, **options: Any) -> tuple[uuid.UUID, uuid.UUID]:
    """A user whose refresh found nothing new, so its outputs are due, committed."""
    user_id, refresh_id = await committed(**options)
    async with database() as session:
        inputs = await begin_proposals(session, refresh_id, now=NOW)
        assert inputs is not None
        await record_proposals(session, inputs, Synthesis(change_set=None), now=NOW)
    return user_id, refresh_id


async def stored(user_id: uuid.UUID) -> tuple[LinkedInPack | None, LinkedInPack | None]:
    """The user's pack and the draft of the one being written."""
    async with database() as session:
        return await get_linkedin_pack(session, user_id), await get_linkedin_draft(session, user_id)


async def test_the_pack_job_writes_the_pack_from_the_refresh_version(
    committed: Committed, claude: Callable[..., FakeMessages]
) -> None:
    """FR-REV-7, FR-LIN-1: every section written and verified from the version the outputs are
    to show, and saved as the user's pack. The master resume isn't touched."""
    user_id, refresh_id = await due_committed(committed)
    fake = claude(*pack_replies(*PACK))
    await run_pack_update(refresh_id, last_attempt=never_last)
    assert [request["model"] for request in fake.requests] == ["standard-model"] * 8
    pack, draft = await stored(user_id)
    assert pack is not None and draft is None
    assert pack.profile_version == 1
    assert [section.text for section in pack.sections] == list(PACK.values())
    assert all(section.report and section.report.passed for section in pack.sections)
    row = await committed_row(refresh_id)
    assert (row.linkedin_pack, row.outputs, row.master_pdf_sha256) == ("updated", "pending", None)
    async with database() as session:
        calls = await session.scalars(select(LlmCallRow.task).where(LlmCallRow.user_id == user_id))
        assert set(calls) == {"linkedin_pack", "claim_verification"}
    await run_pack_update(refresh_id, last_attempt=never_last)
    assert len(fake.requests) == 8, "once"


async def test_the_pack_job_writes_a_section_again_without_a_confidential_term(
    committed: Committed, claude: Callable[..., FakeMessages]
) -> None:
    """T-049: the owner's confidential terms reach the pack a refresh writes, as they do
    `wj linkedin --db`'s."""
    user_id, refresh_id = await due_committed(committed)
    async with database() as session:
        await save_confidential_terms(session, user_id, ConfidentialTerms(terms=("sensor logs",)))
    clean = "I built a Python tool that labelled logs."
    fake = claude(
        *pack_replies("headline", "about", "position:northwind"),
        reply(WrittenSection(text=PACK["position:contoso"])),
        reply(SUPPORTED),
        reply(WrittenSection(text=clean)),
        reply(SUPPORTED),
    )
    await run_pack_update(refresh_id, last_attempt=never_last)
    assert len(fake.requests) == 10
    pack, _ = await stored(user_id)
    assert pack is not None
    contoso = pack.sections[3]
    assert (contoso.text, contoso.flagged) == (clean, False)
    assert contoso.report is not None and contoso.report.attempts == 2


async def test_a_refresh_with_nothing_new_brings_the_pack_to_the_newest_version(
    committed: Committed, claude: Callable[..., FakeMessages]
) -> None:
    """FR-REV-7: an edit the owner made reaches the pack. As with `wj linkedin --db`, only the
    sections that changed since the pack marked done are written; the others are carried over
    without a call (FR-LIN-2)."""
    user_id, first = await due_committed(committed)
    claude(*pack_replies(*PACK))
    await run_pack_update(first, last_attempt=never_last)
    edited = PROFILE.model_dump(mode="json")
    for bullet in edited["work"][1]["highlights"]:
        if bullet["id"] == "ct_datasets":
            bullet["text"] = "Surveyed 40 open datasets for the study."
    async with database() as session:
        pack = await get_linkedin_pack(session, user_id)
        assert pack is not None
        await save_linkedin_done(session, user_id, mark_done(pack, None, now=NOW)[0])
        await save_version(session, user_id, Profile.model_validate(edited), author="owner")
        second = await start_refresh(session, user_id)
        assert isinstance(second, uuid.UUID)
        inputs = await begin_proposals(session, second, now=NOW)
        assert inputs is not None
        await record_proposals(session, inputs, Synthesis(change_set=None), now=NOW)

    fake = claude(*pack_replies("about", "position:contoso"))
    await run_pack_update(second, last_attempt=never_last)
    assert len(fake.requests) == 4
    pack, _ = await stored(user_id)
    assert pack is not None and pack.profile_version == 2
    assert [section.key for section in pack.sections if section.report is None] == [
        "headline",
        "position:northwind",
    ]
    assert (await committed_row(second)).linkedin_pack == "updated"
    assert (await committed_row(first)).linkedin_pack == "updated", "written before"


async def test_a_refusal_fails_the_pack_at_once(
    committed: Committed, claude: Callable[..., FakeMessages]
) -> None:
    """It won't change on a retry. The sections written before it stay for the next try, and
    no pack is saved."""
    user_id, refresh_id = await due_committed(committed)
    claude(*pack_replies("headline"), refusal(), refusal())
    await run_pack_update(refresh_id, last_attempt=never_last)
    row = await committed_row(refresh_id)
    assert (row.linkedin_pack, row.outputs) == ("failed", "pending")
    pack, draft = await stored(user_id)
    assert pack is None
    assert draft is not None and [section.key for section in draft.sections] == ["headline"]


async def test_a_try_stopped_part_way_keeps_its_sections_for_the_retry(
    committed: Committed, claude: Callable[..., FakeMessages]
) -> None:
    """Such as one the tick stops at its time limit, or an error that may pass: the retry writes
    only the sections still missing. On the last try, the pack is marked failed."""
    user_id, refresh_id = await due_committed(committed)
    claude(*pack_replies("headline", "about"), RuntimeError("connection reset"))
    with pytest.raises(RuntimeError):
        await run_pack_update(refresh_id, last_attempt=never_last)
    assert (await committed_row(refresh_id)).linkedin_pack == "pending", "to retry"
    pack, draft = await stored(user_id)
    assert pack is None
    assert draft is not None and [section.key for section in draft.sections] == [
        "headline",
        "about",
    ]

    claude(RuntimeError("connection reset"))
    with pytest.raises(RuntimeError):
        await run_pack_update(refresh_id, last_attempt=lambda error: True)
    assert (await committed_row(refresh_id)).linkedin_pack == "failed"

    async with database() as session:
        await session.execute(
            update(Refresh).where(Refresh.id == refresh_id).values(linkedin_pack="pending")
        )
    fake = claude(*pack_replies("position:northwind", "position:contoso"))
    await run_pack_update(refresh_id, last_attempt=never_last)
    assert len(fake.requests) == 4
    pack, draft = await stored(user_id)
    assert pack is not None and draft is None
    assert [section.text for section in pack.sections] == list(PACK.values())


class SlowMessages(FakeMessages):
    """Waits at the request numbered `at` until `go` is set."""

    def __init__(self, *replies: BetaMessage, at: int) -> None:
        super().__init__(*replies)
        self.at = at
        self.waiting = threading.Event()
        self.go = threading.Event()

    def stream(self, **request: Any) -> Any:
        if len(self.requests) + 1 == self.at:
            self.waiting.set()
            self.go.wait(timeout=10)
        return super().stream(**request)


async def test_a_pack_stopped_at_the_time_limit_is_retried_from_where_it_stopped(
    committed: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tick stops a job that runs past its time; the sections written stay."""
    user_id, refresh_id = await due_committed(committed)
    slow = SlowMessages(*pack_replies(*PACK), at=5)
    monkeypatch.setattr(job_module, "_llm_client", lambda log: LLMClient(llm_settings(), slow, log))
    job = asyncio.create_task(run_pack_update(refresh_id, last_attempt=never_last))
    try:
        while not slow.waiting.is_set():
            await asyncio.sleep(0.01)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
    finally:
        slow.go.set()
    assert (await committed_row(refresh_id)).linkedin_pack == "pending", "to retry"
    _, draft = await stored(user_id)
    assert draft is not None and [section.key for section in draft.sections] == [
        "headline",
        "about",
    ]


async def test_a_tick_without_latex_writes_the_pack(
    committed: Committed,
    claude: Callable[..., FakeMessages],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Such as Vercel's: the pack doesn't wait for the master resume, which does (C6)."""
    user_id, refresh_id = await due_committed(committed)
    claude(*pack_replies(*PACK))
    monkeypatch.setattr("app.tick.worker_queues", lambda: ["default"])
    connector = InMemoryConnector()
    with jobs.replace_connector(connector):
        await tick(now=NOW.timestamp())
    pack, _ = await stored(user_id)
    assert pack is not None and pack.profile_version == 1
    row = await committed_row(refresh_id)
    assert (row.linkedin_pack, row.outputs) == ("updated", "pending")
    waiting = [job["task_name"] for job in connector.jobs.values() if job["status"] == "todo"]
    assert waiting == ["update_outputs"]
    async with database() as session:
        keys = await session.scalars(select(Setting.key).where(Setting.user_id == user_id))
        assert sorted(keys) == ["linkedin_pack"], "no draft left"
