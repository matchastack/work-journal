"""Facts, variants, job postings, applications, artifacts and LLM calls in the database."""

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import User
from app.db.store import (
    delete_variant,
    get_artifact,
    get_fact,
    get_job_posting,
    get_variant,
    list_applications,
    list_facts,
    list_variants,
    llm_cost_usd,
    record_llm_call,
    save_application,
    save_facts,
    save_job_posting,
    save_variant,
    store_artifact,
)
from app.schema.fact import ChangeValue, Fact, Metric
from app.schema.jobs import Application, JobPosting
from app.schema.variant import Variant

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
