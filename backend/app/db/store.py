"""The engine's other data: facts, variants, job postings, applications, artifacts and LLM calls.

Facts are stored encrypted (FR-JRN-4). Artifacts, such as resume PDFs, are stored once under
their SHA-256 hash.
"""

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import JsonValue
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ApplicationRow,
    Artifact,
    FactRow,
    JobPostingRow,
    LlmCallRow,
    VariantRow,
)
from app.schema.fact import Fact
from app.schema.jobs import Application, JobPosting
from app.schema.variant import Variant


@dataclass(frozen=True)
class SavedPosting:
    id: uuid.UUID
    text: str
    """The posting as pasted."""
    posting: JobPosting


async def save_facts(session: AsyncSession, user_id: uuid.UUID, facts: Sequence[Fact]) -> None:
    """Store facts, replacing any stored under the same IDs."""
    if not facts:
        return
    rows = [
        {
            "user_id": user_id,
            "id": fact.id,
            "data": fact.model_dump_json(by_alias=True),
            "origin": fact.origin,
            "entry_id": fact.entry_id,
            "role_id": fact.role_id,
            "project_id": fact.project_id,
        }
        for fact in facts
    ]
    query = insert(FactRow).values(rows)
    replaced = {name: query.excluded[name] for name in rows[0] if name not in ("user_id", "id")}
    await session.execute(
        query.on_conflict_do_update(index_elements=[FactRow.user_id, FactRow.id], set_=replaced)
    )


async def get_fact(session: AsyncSession, user_id: uuid.UUID, fact_id: str) -> Fact | None:
    data = await session.scalar(
        select(FactRow.data).where(FactRow.user_id == user_id, FactRow.id == fact_id)
    )
    return None if data is None else Fact.model_validate_json(data)


async def list_facts(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    role_id: str | None = None,
    project_id: str | None = None,
) -> list[Fact]:
    """The user's facts, optionally only those about one role or project, by ID."""
    query = select(FactRow.data).where(FactRow.user_id == user_id)
    if role_id is not None:
        query = query.where(FactRow.role_id == role_id)
    if project_id is not None:
        query = query.where(FactRow.project_id == project_id)
    rows = await session.scalars(query.order_by(FactRow.id))
    return [Fact.model_validate_json(data) for data in rows]


async def save_variant(session: AsyncSession, user_id: uuid.UUID, variant: Variant) -> None:
    """Store a variant, replacing any stored under the same ID."""
    data = variant.model_dump(mode="json", by_alias=True)
    query = insert(VariantRow).values(user_id=user_id, id=variant.id, data=data)
    await session.execute(
        query.on_conflict_do_update(
            index_elements=[VariantRow.user_id, VariantRow.id],
            set_={"data": query.excluded.data, "updated_at": func.now()},
        )
    )


async def get_variant(session: AsyncSession, user_id: uuid.UUID, variant_id: str) -> Variant | None:
    data = await session.scalar(
        select(VariantRow.data).where(VariantRow.user_id == user_id, VariantRow.id == variant_id)
    )
    return None if data is None else Variant.model_validate(data)


async def list_variants(session: AsyncSession, user_id: uuid.UUID) -> list[Variant]:
    rows = await session.scalars(
        select(VariantRow.data).where(VariantRow.user_id == user_id).order_by(VariantRow.id)
    )
    return [Variant.model_validate(data) for data in rows]


async def delete_variant(session: AsyncSession, user_id: uuid.UUID, variant_id: str) -> bool:
    """Delete a variant. Returns whether there was one."""
    result = await session.execute(
        delete(VariantRow)
        .where(VariantRow.user_id == user_id, VariantRow.id == variant_id)
        .returning(VariantRow.id)
    )
    return result.scalar_one_or_none() is not None


async def save_job_posting(
    session: AsyncSession, user_id: uuid.UUID, text: str, posting: JobPosting
) -> uuid.UUID:
    """Store a job posting as pasted, with its parsed form."""
    row = JobPostingRow(
        user_id=user_id, text=text, posting=posting.model_dump(mode="json", by_alias=True)
    )
    session.add(row)
    await session.flush()
    return row.id


async def get_job_posting(
    session: AsyncSession, user_id: uuid.UUID, posting_id: uuid.UUID
) -> SavedPosting | None:
    row = await session.scalar(
        select(JobPostingRow).where(
            JobPostingRow.user_id == user_id, JobPostingRow.id == posting_id
        )
    )
    if row is None:
        return None
    return SavedPosting(id=row.id, text=row.text, posting=JobPosting.model_validate(row.posting))


async def save_application(
    session: AsyncSession,
    user_id: uuid.UUID,
    application: Application,
    *,
    posting_id: uuid.UUID | None = None,
    verifier_report: Mapping[str, JsonValue] | None = None,
) -> None:
    """Log a tailored resume (FR-TLR-8). Its PDF must be stored first, with `store_artifact`."""
    session.add(
        ApplicationRow(
            user_id=user_id,
            id=application.id,
            data=application.model_dump(mode="json", by_alias=True),
            applied_on=application.applied_on,
            posting_id=posting_id,
            pdf_sha256=application.pdf_sha256,
            verifier_report=None if verifier_report is None else dict(verifier_report),
        )
    )
    await session.flush()


async def list_applications(session: AsyncSession, user_id: uuid.UUID) -> list[Application]:
    """The user's applications, newest first."""
    rows = await session.scalars(
        select(ApplicationRow.data)
        .where(ApplicationRow.user_id == user_id)
        .order_by(ApplicationRow.applied_on.desc(), ApplicationRow.created_at.desc())
    )
    return [Application.model_validate(data) for data in rows]


async def store_artifact(
    session: AsyncSession, user_id: uuid.UUID, data: bytes, media_type: str
) -> str:
    """Store a file once per user, and return its SHA-256 hash, which names it."""
    sha256 = hashlib.sha256(data).hexdigest()
    await session.execute(
        insert(Artifact)
        .values(user_id=user_id, sha256=sha256, media_type=media_type, data=data)
        .on_conflict_do_nothing(index_elements=[Artifact.user_id, Artifact.sha256])
    )
    return sha256


async def get_artifact(
    session: AsyncSession, user_id: uuid.UUID, sha256: str
) -> tuple[str, bytes] | None:
    """The media type and contents of a stored file."""
    row = (
        await session.execute(
            select(Artifact.media_type, Artifact.data).where(
                Artifact.user_id == user_id, Artifact.sha256 == sha256
            )
        )
    ).one_or_none()
    return None if row is None else (row[0], bytes(row[1]))


async def record_llm_call(
    session: AsyncSession, user_id: uuid.UUID, call: Mapping[str, Any]
) -> None:
    """Log an LLM call: the fields of the LLM client's call record (NFR-COST-1)."""
    values = dict(call)
    if (cost := values.get("cost_usd")) is not None:
        values["cost_usd"] = Decimal(str(cost))
    await session.execute(insert(LlmCallRow).values(user_id=user_id, **values))


async def llm_cost_usd(session: AsyncSession, user_id: uuid.UUID) -> Decimal:
    """What the user's logged LLM calls cost, where their tier has a price."""
    total = await session.scalar(
        select(func.coalesce(func.sum(LlmCallRow.cost_usd), 0)).where(LlmCallRow.user_id == user_id)
    )
    return Decimal(total or 0)
