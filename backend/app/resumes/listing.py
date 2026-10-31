"""The owner's resumes, as the Resumes page lists them (FR-RES-5, FR-RES-6, FR-TLR-8).

- The master document from the latest refresh whose outputs fell due (`app/refresh.py`), with
  its PDF once a computer with TeX Live has rendered it (C6), and the profile check (`wj lint`) of
  the version it shows. While the latest isn't rendered, the newest one that was is listed too.
- Every tailored resume, newest first, with the resume rules' findings kept with its
  application: one with an error is listed, but marked not to send (R2).

Each PDF's page count, cut report and warnings come from its render (`renders`), found by the
PDF's hash; a PDF stored before renders were kept has none. The API never runs LaTeX: it only
reads what was rendered on the owner's computer.
"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApplicationRow, ProfileVersionRow, Refresh
from app.db.profiles import VersionError, get_version
from app.db.store import PDF, RenderInfo, get_artifact, renders_of
from app.schema.common import Model
from app.schema.jobs import Application
from app.tailoring.applications import ResumeChecks
from app.validate.lint import LintReport, lint_profile

MasterStatus = Literal["pending", "updated", "failed"]
"""Waiting for a computer with TeX Live to render it, rendered, or failed to render."""


class ResumeCut(Model):
    """Content cut to fit the page limit (FR-RES-7)."""

    kind: Literal["bullet", "role", "project"]
    id: str
    text: str


class ResumePdf(Model):
    sha256: str
    """The PDF's SHA-256 hash, which names it: `/api/resumes/pdf/{sha256}`."""
    pages: int | None
    """None when it wasn't recorded."""
    cuts: tuple[ResumeCut, ...] | None
    """What was cut to fit the page limit, in the order it was cut; None when it wasn't
    recorded, as for a PDF made before the Resumes page."""
    warnings: tuple[str, ...] = ()


class MasterResume(Model):
    """The full master document (FR-RES-6) of one refresh (FR-RES-5)."""

    version: int
    """The profile version it shows."""
    status: MasterStatus
    changed_at: datetime
    """When it fell due, or was rendered or failed."""
    pdf: ResumePdf | None
    """None until it's rendered."""
    lint: LintReport | None
    """The profile check (`wj lint`) of the version; None when the version is gone."""


class TailoredResume(Model):
    """A resume tailored to a job posting, as its application logged it (FR-TLR-8)."""

    id: str
    """The application's ID."""
    company: str
    position: str
    applied_on: date
    logged_at: datetime
    pdf: ResumePdf | None
    rules: LintReport | None
    """The resume rules' findings; None when they weren't kept."""
    sendable: bool
    """Whether it may be sent: its rules were checked and found no error (R2)."""


class Resumes(Model):
    profile_version: int | None
    """The newest profile version: a refresh brings the master resume up to it (FR-REV-7)."""
    master: MasterResume | None
    """The master document from the latest refresh; None before any refresh's outputs fell
    due."""
    last_rendered: MasterResume | None
    """The newest master document that was rendered, while `master` isn't."""
    tailored: tuple[TailoredResume, ...]
    """Newest first."""


async def load_resumes(session: AsyncSession, user_id: uuid.UUID) -> Resumes:
    profile_version = await session.scalar(
        select(func.max(ProfileVersionRow.number)).where(ProfileVersionRow.user_id == user_id)
    )
    newest = (
        select(Refresh)
        .where(Refresh.user_id == user_id)
        .order_by(Refresh.outputs_at.desc().nulls_last(), Refresh.requested_at.desc())
        .limit(1)
    )
    latest = await session.scalar(
        newest.where(Refresh.outputs.in_(("pending", "updated", "failed")))
    )
    rendered = None
    if latest is not None and latest.outputs != "updated":
        rendered = await session.scalar(newest.where(Refresh.outputs == "updated"))
    applications = list(
        await session.execute(
            select(
                ApplicationRow.data,
                ApplicationRow.created_at,
                ApplicationRow.pdf_sha256,
                ApplicationRow.verifier_report,
            )
            .where(ApplicationRow.user_id == user_id)
            .order_by(ApplicationRow.applied_on.desc(), ApplicationRow.created_at.desc())
        )
    )
    pdfs = {row.pdf_sha256 for row in applications if row.pdf_sha256}
    pdfs |= {
        refresh.master_pdf_sha256
        for refresh in (latest, rendered)
        if refresh is not None and refresh.master_pdf_sha256
    }
    renders = await renders_of(session, user_id, pdfs)
    tailored: list[TailoredResume] = []
    for data, logged_at, pdf_sha256, report in applications:
        application = Application.model_validate(data)
        rules = _rules(report)
        tailored.append(
            TailoredResume(
                id=application.id,
                company=application.company,
                position=application.position,
                applied_on=application.applied_on,
                logged_at=logged_at,
                pdf=None if pdf_sha256 is None else _pdf(pdf_sha256, renders.get(pdf_sha256)),
                rules=rules,
                sendable=rules is not None and rules.ok,
            )
        )
    return Resumes(
        profile_version=profile_version,
        master=await _master(session, latest, renders),
        last_rendered=await _master(session, rendered, renders),
        tailored=tuple(tailored),
    )


async def resume_pdf(
    session: AsyncSession, user_id: uuid.UUID, sha256: str
) -> tuple[str, bytes] | None:
    """A PDF the Resumes page lists, by its hash, with a file name for it: the application's ID
    for a tailored resume, or the version for the master. None for any other file."""
    application_id = await session.scalar(
        select(ApplicationRow.id)
        .where(ApplicationRow.user_id == user_id, ApplicationRow.pdf_sha256 == sha256)
        .order_by(ApplicationRow.created_at.desc())
        .limit(1)
    )
    if application_id is not None:
        name = f"{application_id}.pdf"
    else:
        version = await session.scalar(
            select(Refresh.output_version)
            .where(Refresh.user_id == user_id, Refresh.master_pdf_sha256 == sha256)
            .order_by(Refresh.output_version.desc())
            .limit(1)
        )
        if version is None:
            return None
        name = f"master-resume-v{version}.pdf"
    stored = await get_artifact(session, user_id, sha256)
    if stored is None or stored[0] != PDF:
        return None
    return name, stored[1]


async def _master(
    session: AsyncSession, refresh: Refresh | None, renders: dict[str, RenderInfo]
) -> MasterResume | None:
    if refresh is None or refresh.outputs not in ("pending", "updated", "failed"):
        return None
    version = refresh.output_version
    assert version is not None, "a refresh's outputs fall due with their version"
    try:
        lint = lint_profile((await get_version(session, refresh.user_id, version)).profile)
    except VersionError:
        lint = None
    pdf = None
    if refresh.master_pdf_sha256 is not None:
        sha256 = refresh.master_pdf_sha256
        pdf = _pdf(sha256, renders.get(sha256), pages=refresh.master_pages)
    return MasterResume(
        version=version,
        status=refresh.outputs,
        changed_at=refresh.outputs_at or refresh.requested_at,
        pdf=pdf,
        lint=lint,
    )


def _pdf(sha256: str, render: RenderInfo | None, *, pages: int | None = None) -> ResumePdf:
    if render is None:
        return ResumePdf(sha256=sha256, pages=pages, cuts=None)
    return ResumePdf(
        sha256=sha256,
        pages=render.pages,
        cuts=tuple(ResumeCut(kind=cut.kind, id=cut.id, text=cut.text) for cut in render.cuts),
        warnings=render.warnings,
    )


def _rules(report: dict[str, object] | None) -> LintReport | None:
    """The resume rules' findings kept with an application, if they were."""
    if report is None:
        return None
    try:
        return ResumeChecks.model_validate(report).rules
    except ValidationError:
        return None
