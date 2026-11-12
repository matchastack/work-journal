"""What the Tailor page shows: each draft, and the application history (T-045).

A draft's view is worked out from the row each time it's read: its resume comes from the profile
version it was tailored from, the plan and wording the jobs kept, and the owner's choices
(`draft_resume`), so the resume rules and the skills to decide are always current. What only a
render can tell, such as the page count, the cuts and the ATS check, comes from the latest render
of those choices.
"""

import uuid
from datetime import date, datetime

from app.db.models import DraftStatus, RenderStatus
from app.resumes.listing import ResumeCut, ResumePdf, TailoredResume
from app.schema.common import Model
from app.schema.jobs import JobPosting
from app.schema.profile import SkillLine
from app.schema.verification import VerifierReport
from app.tailoring.ats import AtsReport
from app.tailoring.coverage import CoverageReport
from app.tailoring.drafts import BulletUse, SkillDecision
from app.validate.lint import LintReport


class DraftPosting(Model):
    """A draft's parsed posting, as stored."""

    posting: JobPosting
    dropped: tuple[str, ...] = ()
    """Skills and terms the model returned that the posting doesn't contain."""


class RenderChecks(Model):
    """What a draft's render found, as stored."""

    pages: int
    cuts: tuple[ResumeCut, ...] = ()
    warnings: tuple[str, ...] = ()
    coverage: CoverageReport
    """The keyword coverage of what the PDF shows, after the cuts."""
    ats: AtsReport


class DraftWords(Model):
    """A bullet with new words to choose from (FR-TLR-3)."""

    id: str
    under: str
    """The role's organisation or the project's name."""
    original: str
    """The bullet's own words."""
    use: BulletUse
    swap: VerifierReport | None
    """An approved swap worked in for the posting, with what the verifier found in it."""
    phrasing: VerifierReport | None
    """A new phrasing in the posting's words, with what the verifier found in it."""
    approved: bool
    """Whether the new phrasing is approved, and saved as a swap on the bullet (FR-WRT-5)."""


class DraftSkill(Model):
    """A skill marked verify-before-shipping that the draft lists or names (R3)."""

    name: str
    decision: SkillDecision | None
    """None until the owner confirms or drops it."""
    locations: tuple[str, ...]
    """Where the resume lists or names it now."""


class OutlineBullet(Model):
    id: str
    text: str


class OutlineItem(Model):
    """A role or project the draft shows."""

    id: str
    name: str
    title: str | None = None
    """A role's title."""
    bullets: tuple[OutlineBullet, ...] = ()


class DraftOutline(Model):
    """What the draft shows before it's fitted to one page."""

    summary: str | None = None
    work: tuple[OutlineItem, ...] = ()
    projects: tuple[OutlineItem, ...] = ()
    skills: tuple[SkillLine, ...] = ()


class DraftRender(Model):
    """The render of the resume the owner's choices make."""

    status: RenderStatus
    """`waiting` for a computer with TeX Live (C6), `rendered` or `failed`."""
    pdf: ResumePdf | None = None
    """Once rendered: `/api/tailor/{id}/pdf` previews it."""
    ats: AtsReport | None = None
    """Once rendered: the ATS check of the PDF's text (FR-TLR-9)."""
    error: str | None = None
    """Why it failed."""


class DraftApplication(Model):
    """The application a downloaded draft was logged as (FR-TLR-8)."""

    id: str
    applied_on: date
    pdf_sha256: str
    """`/api/resumes/pdf/{sha256}` downloads it."""
    sendable: bool
    """Whether it may be sent: no resume rule found an error (R2)."""


class TailorDraft(Model):
    """A resume being tailored to a job posting."""

    id: uuid.UUID
    created_at: datetime
    status: DraftStatus
    """`parsing` the posting, `tailoring` the resume, `ready` for the owner, or `failed`."""
    error: str | None
    """Why it failed, or why it can't be shown."""
    posting: JobPosting | None
    """The parsed requirements, once parsed (FR-TLR-1)."""
    dropped_terms: tuple[str, ...] = ()
    """Terms the model returned that the posting doesn't contain, so they were left out."""
    profile_version: int | None = None
    """The profile version it was tailored from."""
    role_type: str | None = None
    notes: tuple[str, ...] = ()
    """What code changed in or left out of the model's plan, and why."""
    bullets: tuple[DraftWords, ...] = ()
    skills: tuple[DraftSkill, ...] = ()
    outline: DraftOutline | None = None
    rules: LintReport | None = None
    """The resume rules' findings. An error doesn't stop the download, but the resume mustn't be
    sent until it's fixed (R2)."""
    coverage: CoverageReport | None = None
    """The keyword coverage report (FR-TLR-6): of the PDF once it's rendered, before that of the
    draft before fitting (`coverage_fitted`)."""
    coverage_fitted: bool = False
    render: DraftRender | None = None
    blocked: tuple[str, ...] = ()
    """Why the PDF can't be downloaded yet; empty when it can."""
    application: DraftApplication | None = None


class DraftSummary(Model):
    """A draft in the list of drafts not yet downloaded."""

    id: uuid.UUID
    created_at: datetime
    status: DraftStatus
    title: str | None
    company: str | None
    render_status: RenderStatus | None


class TailorPage(Model):
    drafts: tuple[DraftSummary, ...]
    """The drafts not yet downloaded, newest first."""
    applications: tuple[TailoredResume, ...]
    """The application history, newest first: each tailored resume, with its PDF."""
