"""Facts, variants, job postings, applications, artifacts, renders and LLM calls in the
database."""

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import User
from app.db.store import (
    RenderInfo,
    delete_variant,
    find_render,
    get_artifact,
    get_fact,
    get_job_posting,
    get_variant,
    list_applications,
    list_facts,
    list_variants,
    llm_cost_usd,
    record_llm_call,
    renders_of,
    save_application,
    save_facts,
    save_job_posting,
    save_render,
    save_variant,
    store_artifact,
)
from app.llm.usage import CallRecord, DatabaseCallLog
from app.render.fit import Cut
from app.render.resume import RenderedResume, StoredRender
from app.schema.fact import ChangeValue, Fact, Metric
from app.schema.jobs import Application, JobPosting
from app.schema.profile import Profile
from app.schema.variant import Variant
from app.selection import MASTER_VARIANT, select

pytestmark = pytest.mark.anyio

EXPORT = Fact(
    id="nw_export",
    statement="Cut the nightly export job from 50 to 12 minutes by batching database writes.",
    metrics=(
        Metric(subject="nightly export", value=ChangeValue(before=50, after=12), unit="minutes"),
    ),
    role_id="northwind",
    origin="import",
)
SENSORS = Fact(
    id="ct_sensors",
    statement="Built a tool that labels sensor logs.",
    role_id="contoso",
    origin="import",
)
POSTING = JobPosting(title="Backend Engineer", company="Fabrikam", must_have=("Python",))
PROFILE = Profile.model_validate_json(
    (Path(__file__).parents[1] / "fixtures" / "profile.json").read_text(encoding="utf-8")
)


@pytest.fixture
async def user(session: AsyncSession) -> AsyncIterator[uuid.UUID]:
    use_key_ring(KeyRing.parse(new_key("k1")))
    row = User()
    session.add(row)
    await session.flush()
    yield row.id
    use_key_ring(None)


async def test_facts_are_ciphertext_at_rest(session: AsyncSession, user: uuid.UUID) -> None:
    """FR-JRN-4: a database dump shows no fact text."""
    await save_facts(session, user, [EXPORT])
    raw = await session.scalar(text("SELECT data FROM facts WHERE user_id = :user"), {"user": user})
    assert isinstance(raw, bytes)
    assert b"export" not in raw
    assert await get_fact(session, user, "nw_export") == EXPORT


async def test_facts_can_be_listed_by_role(session: AsyncSession, user: uuid.UUID) -> None:
    await save_facts(session, user, [EXPORT, SENSORS])
    assert await list_facts(session, user) == [SENSORS, EXPORT]
    assert await list_facts(session, user, role_id="northwind") == [EXPORT]
    assert await list_facts(session, user, project_id="recipe_box") == []


async def test_saving_a_fact_again_replaces_it(session: AsyncSession, user: uuid.UUID) -> None:
    await save_facts(session, user, [EXPORT])
    changed = EXPORT.model_copy(update={"statement": "Cut the export job to 12 minutes."})
    await save_facts(session, user, [changed])
    assert await list_facts(session, user) == [changed]


async def test_saving_a_fact_again_records_when_it_changed(
    session: AsyncSession, user: uuid.UUID
) -> None:
    """FR-REV-6: an edited fact waits for the next refresh again, as a new one does."""
    await save_facts(session, user, [EXPORT])
    earlier = datetime(2026, 1, 1, tzinfo=UTC)
    when = text("SELECT created_at, updated_at, refreshed_at FROM facts WHERE user_id = :user")
    created_at, updated_at, refreshed_at = (await session.execute(when, {"user": user})).one()
    assert (created_at, refreshed_at) == (updated_at, None)
    await session.execute(
        text(
            "UPDATE facts SET updated_at = :earlier, refreshed_at = :earlier WHERE user_id = :user"
        ),
        {"earlier": earlier, "user": user},
    )
    await save_facts(session, user, [EXPORT.model_copy(update={"statement": "Cut it to 12."})])
    _, updated_at, refreshed_at = (await session.execute(when, {"user": user})).one()
    assert updated_at > earlier
    assert refreshed_at is None


async def test_variants_are_saved_listed_and_deleted(
    session: AsyncSession, user: uuid.UUID
) -> None:
    backend = Variant(id="backend", name="Backend", role_type="backend")
    await save_variant(session, user, backend)
    renamed = backend.model_copy(update={"name": "Backend roles"})
    await save_variant(session, user, renamed)
    assert await list_variants(session, user) == [renamed]
    assert await get_variant(session, user, "backend") == renamed
    assert await delete_variant(session, user, "backend")
    assert not await delete_variant(session, user, "backend")
    assert await get_variant(session, user, "backend") is None


async def test_an_artifact_is_stored_once_under_its_hash(
    session: AsyncSession, user: uuid.UUID
) -> None:
    pdf = b"%PDF-1.5 a fictional resume"
    sha256 = await store_artifact(session, user, pdf, "application/pdf")
    assert sha256 == hashlib.sha256(pdf).hexdigest()
    assert await store_artifact(session, user, pdf, "application/pdf") == sha256
    assert await get_artifact(session, user, sha256) == ("application/pdf", pdf)
    assert await get_artifact(session, user, "0" * 64) is None


def rendered(pdf: bytes, key: str, *cuts: Cut, reused: bool = False) -> RenderedResume:
    """A render of the fixture profile's master document, as LaTeX would make it."""
    return RenderedResume(
        pdf=pdf,
        tex="\\documentclass{article}",
        pages=2,
        text="Casey Morgan",
        cuts=cuts,
        warnings=("Some text uses a bitmap font.",),
        selection=select(PROFILE, MASTER_VARIANT),
        key=key,
        reused=reused,
    )


async def test_a_render_is_kept_under_its_key(session: AsyncSession, user: uuid.UUID) -> None:
    """T-044: renders are cached by content hash. What was cut is kept with the PDF, so it can be
    rebuilt without compiling, and listed."""
    key = "a" * 64
    assert await find_render(session, user, key) is None
    cuts = (
        Cut(kind="bullet", id="ct_datasets", text="Labelled the datasets."),
        Cut(kind="bullet", id="ct_sensors", text="Built the sensor tool."),
        Cut(kind="role", id="contoso", text="Contoso Research"),
    )
    pdf = b"%PDF-1.5 the master document"
    sha256 = await save_render(session, user, rendered(pdf, key, *cuts))
    assert sha256 == hashlib.sha256(pdf).hexdigest()
    assert await get_artifact(session, user, sha256) == ("application/pdf", pdf)
    assert await find_render(session, user, key) == StoredRender(pdf=pdf, bullet_cuts=2)
    assert await renders_of(session, user, [sha256, "b" * 64]) == {
        sha256: RenderInfo(pages=2, cuts=cuts, warnings=("Some text uses a bitmap font.",))
    }
    assert await renders_of(session, user, []) == {}


async def test_a_render_is_kept_once(session: AsyncSession, user: uuid.UUID) -> None:
    key = "c" * 64
    first = await save_render(session, user, rendered(b"%PDF-1.5 first", key))
    second = await save_render(session, user, rendered(b"%PDF-1.5 second", key))
    assert second == hashlib.sha256(b"%PDF-1.5 second").hexdigest()
    stored = await find_render(session, user, key)
    assert stored == StoredRender(pdf=b"%PDF-1.5 first", bullet_cuts=0)
    assert list(await renders_of(session, user, [first, second])) == [first]


async def test_a_reused_render_stores_nothing_new(session: AsyncSession, user: uuid.UUID) -> None:
    pdf = b"%PDF-1.5 reused"
    sha256 = await save_render(session, user, rendered(pdf, "d" * 64, reused=True))
    assert sha256 == hashlib.sha256(pdf).hexdigest()
    assert await get_artifact(session, user, sha256) is None
    assert await find_render(session, user, "d" * 64) is None


async def test_renders_belong_to_their_user(session: AsyncSession, user: uuid.UUID) -> None:
    other = User()
    session.add(other)
    await session.flush()
    sha256 = await save_render(session, other.id, rendered(b"%PDF-1.5 theirs", "e" * 64))
    assert await find_render(session, user, "e" * 64) is None
    assert await renders_of(session, user, [sha256]) == {}


def application(pdf_sha256: str | None) -> Application:
    return Application(
        id="fabrikam_backend",
        applied_on=date(2026, 10, 1),
        company="Fabrikam",
        position="Backend Engineer",
        posting=POSTING,
        posting_text="Backend Engineer at Fabrikam. Must have: Python.",
        title_variant="Software Engineer",
        bullet_ids=("nw_queue",),
        pdf_sha256=pdf_sha256,
    )


async def test_an_application_keeps_its_posting_report_and_pdf(
    session: AsyncSession, user: uuid.UUID
) -> None:
    """FR-TLR-8 and FR-FID-8."""
    text_ = "Backend Engineer at Fabrikam. Must have: Python."
    posting_id = await save_job_posting(session, user, text_, POSTING)
    saved = await get_job_posting(session, user, posting_id)
    assert saved is not None
    assert (saved.text, saved.posting) == (text_, POSTING)
    sha256 = await store_artifact(session, user, b"%PDF-1.5 tailored", "application/pdf")
    report = {"claims": "ok"}
    await save_application(
        session, user, application(sha256), posting_id=posting_id, verifier_report=report
    )
    assert await list_applications(session, user) == [application(sha256)]


async def test_an_application_needs_its_pdf_stored_first(
    session: AsyncSession, user: uuid.UUID
) -> None:
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await save_application(session, user, application("a" * 64))


async def test_llm_calls_are_logged_with_their_cost(session: AsyncSession, user: uuid.UUID) -> None:
    """NFR-COST-1, with the fields of the LLM client's call record."""
    call = {
        "at": datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
        "task": "fact_extraction",
        "tier": "standard",
        "model": "model-standard",
        "prompt": "fact_extraction/v1",
        "outcome": "ok",
        "attempts": 1,
        "input_tokens": 1200,
        "output_tokens": 300,
        "cost_usd": 0.0054,
        "latency_ms": 2100,
    }
    await record_llm_call(session, user, call)
    await record_llm_call(session, user, {**call, "cost_usd": None, "outcome": "refusal"})
    assert await llm_cost_usd(session, user) == Decimal("0.0054")


async def test_the_llm_clients_call_log_saves_to_the_database(
    session: AsyncSession, user: uuid.UUID
) -> None:
    """The client's records are kept until a job saves them for its user."""
    call = CallRecord(
        at=datetime(2026, 10, 2, 9, 0, tzinfo=UTC),
        task="fact_extraction",
        tier="standard",
        model="model-standard",
        served_by="model-standard",
        prompt="fact_extraction/v1",
        outcome="ok",
        attempts=1,
        input_tokens=1200,
        output_tokens=300,
        cache_read_tokens=4000,
        cost_usd=0.0066,
        latency_ms=2100,
        request_id="req_1",
    )
    log = DatabaseCallLog(user)
    log.record(call)
    log.record(call.model_copy(update={"outcome": "refusal", "cost_usd": None}))
    assert await log.save(session) == 2
    assert await log.save(session) == 0, "saved records aren't saved twice"
    assert await llm_cost_usd(session, user) == Decimal("0.0066")
    rows = await session.execute(
        text("SELECT outcome, cache_read_tokens, request_id FROM llm_calls ORDER BY id")
    )
    assert rows.all() == [("ok", 4000, "req_1"), ("refusal", 4000, "req_1")]
