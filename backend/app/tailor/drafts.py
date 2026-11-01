"""Tailoring drafts in the database: what the Tailor page and its jobs do (T-045).

The API never runs LaTeX or a long model call inside a request (C6, NFR-PERF-1). Pasting a
posting stores a draft, and jobs take it from there, with only its ID as their argument:
1. `parse_draft_posting` parses the posting with the light-tier model (FR-TLR-1), so its
   requirements show before the resume does;
2. `tailor_draft` chooses with the heavy-tier model and words with the standard-tier model, each
   new text checked by the verifier (`draft_tailoring`). It isn't retried, since each try calls
   the heavy-tier model; a try that never finished fails the draft, and the owner can try again;
3. `render_draft` renders the resume the owner's choices make, on the queue only a computer with
   TeX Live takes (`wj worker` or `wj tick` there).

Each change the owner makes points the draft at the render of its new resume (`render_key`). A
render kept under that key is used at once, by reading its PDF back without LaTeX, so choosing
words already rendered compiles nothing; otherwise the draft waits for `render_draft`. A render
is recorded only if the choices haven't changed since it started.

Approving a new phrasing saves it as a swap on its bullet, in the profile's next version, made
by a change set by the owner (FR-WRT-5). Downloading the PDF logs the application once, with the
checks kept beside it (FR-TLR-8, FR-FID-8); a draft that breaks a resume rule can still be
downloaded, but it's marked not to send (R2). A skill marked verify-before-shipping that the
draft lists must be confirmed or dropped first (R3).
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from pypdf.errors import PyPdfError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import profile_ops
from app.db.models import ApplicationRow, TailorDraftRow, User
from app.db.profiles import (
    Version,
    VersionError,
    close_change_set,
    get_version,
    latest_version,
    propose_change_set,
    save_version,
)
from app.db.settings import get_style_notes
from app.db.store import (
    find_render,
    list_facts,
    save_application,
    save_job_posting,
    save_render,
)
from app.render.compile import CompileError
from app.render.fit import FitError
from app.render.resume import RenderedResume, RenderError, StoredRender, render_key, render_resume
from app.resumes.listing import ResumeCut, ResumePdf, tailored_resumes
from app.schema.changes import ChangeSet, ReviewedOp
from app.schema.fact import Fact
from app.schema.jobs import JobPosting
from app.schema.profile import Profile
from app.selection import Selection
from app.tailor.view import (
    DraftApplication,
    DraftOutline,
    DraftPosting,
    DraftRender,
    DraftSkill,
    DraftSummary,
    DraftWords,
    OutlineBullet,
    OutlineItem,
    RenderChecks,
    TailorDraft,
    TailorPage,
)
from app.tailoring.applications import ResumeChecks, pdf_checks, record
from app.tailoring.coverage import coverage
from app.tailoring.drafts import (
    BulletUse,
    ChoiceError,
    DraftChoices,
    DraftResume,
    SkillDecision,
    Tailoring,
    approve,
    decide_skill,
    draft_resume,
    first_choices,
    proposal,
    use_words,
)
from app.tailoring.posting import ParsedPosting
from app.writing import swap_op

UNFINISHED_AFTER = timedelta(minutes=30)
"""How long after a try at tailoring a draft started it counts as unfinished: well past the
300 s a job may run in a tick. Until then, another try leaves it to the one that started it."""
DRAFTS_LISTED = 20
"""How many drafts not yet downloaded the page lists."""


class DraftError(Exception):
    """A request the draft can't take."""


class DraftNotFound(DraftError):
    """The owner has no such draft."""


class DraftConflict(DraftError):
    """The draft isn't in a state that allows the request, such as choosing words before it's
    tailored. The message says why, for the owner."""


class TailoringInterrupted(Exception):
    """An earlier try at tailoring the draft started more than `UNFINISHED_AFTER` ago and never
    finished. It isn't tried again: each try calls the heavy-tier model."""


# --- The page ----------------------------------------------------------------------------------


async def create_draft(session: AsyncSession, user_id: uuid.UUID, text: str) -> uuid.UUID:
    """Store a pasted posting as a new draft, waiting to be parsed."""
    row = TailorDraftRow(user_id=user_id, posting_text=text, choices={})
    session.add(row)
    await session.flush()
    return row.id


async def load_page(session: AsyncSession, user_id: uuid.UUID) -> TailorPage:
    rows = await session.scalars(
        select(TailorDraftRow)
        .where(TailorDraftRow.user_id == user_id, TailorDraftRow.application_id.is_(None))
        .order_by(TailorDraftRow.created_at.desc(), TailorDraftRow.id)
        .limit(DRAFTS_LISTED)
    )
    drafts: list[DraftSummary] = []
    for row in rows:
        posting = _posting(row)
        drafts.append(
            DraftSummary(
                id=row.id,
                created_at=row.created_at,
                status=row.status,
                title=posting.posting.title if posting else None,
                company=posting.posting.company if posting else None,
                render_status=row.render_status,
            )
        )
    return TailorPage(drafts=tuple(drafts), applications=await tailored_resumes(session, user_id))


async def load_draft(session: AsyncSession, user_id: uuid.UUID, draft_id: uuid.UUID) -> TailorDraft:
    row = await session.scalar(
        select(TailorDraftRow).where(
            TailorDraftRow.user_id == user_id, TailorDraftRow.id == draft_id
        )
    )
    if row is None:
        raise DraftNotFound(draft_id)
    return await _view(session, row)


async def discard_draft(session: AsyncSession, user_id: uuid.UUID, draft_id: uuid.UUID) -> None:
    """Delete a draft not yet downloaded. A swap saved from it stays in the profile."""
    row = await _locked(session, user_id, draft_id)
    if row.application_id is not None:
        raise DraftConflict("It's logged as an application, so it's kept.")
    await session.execute(delete(TailorDraftRow).where(TailorDraftRow.id == row.id))


async def retry_draft(session: AsyncSession, user_id: uuid.UUID, draft_id: uuid.UUID) -> None:
    """Try a failed draft again, from parsing its posting if that's what failed, or render its
    resume again if that failed, as LaTeX running out of time can."""
    row = await _locked(session, user_id, draft_id)
    if row.status == "failed":
        row.status = "parsing" if row.posting is None else "tailoring"
        row.error = None
        row.started_at = None
    elif row.render_status == "failed" and row.application_id is None:
        row.render_status = "waiting"
        row.render_error = None
    else:
        raise DraftConflict("Only a draft or a render that failed can be tried again.")


# --- The owner's choices -----------------------------------------------------------------------


async def choose_words(
    session: AsyncSession, user_id: uuid.UUID, draft_id: uuid.UUID, bullet_id: str, use: BulletUse
) -> bool:
    """Choose a reworded bullet's words. Returns whether the new resume waits for a render."""
    draft = await _ready(session, user_id, draft_id)
    try:
        choices = use_words(draft.choices, draft.tailoring, bullet_id, use)
    except ChoiceError as error:
        raise DraftConflict(_sentence(str(error))) from None
    return await _choose(session, draft, choices)


async def approve_phrasing(
    session: AsyncSession, user_id: uuid.UUID, draft_id: uuid.UUID, bullet_id: str
) -> bool:
    """Approve a new phrasing: it's saved as a swap on its bullet, in the profile's next version,
    and the draft uses it (FR-WRT-5). Approving it again changes nothing. Returns whether the new
    resume waits for a render."""
    draft = await _ready(session, user_id, draft_id)
    try:
        offered = proposal(draft.tailoring, bullet_id)
    except ChoiceError as error:
        raise DraftConflict(_sentence(str(error))) from None
    if not offered.report.passed:
        raise DraftConflict("Only a phrasing that passed the verifier can be approved.")
    if bullet_id not in draft.choices.approved:
        op = swap_op(bullet_id, offered.report, style="posting", posting=draft.posting)
        reviewed = ReviewedOp(
            op=op, status="accepted", verifier_report=offered.report.model_dump(mode="json")
        )
        context = op.swap.contexts[0] if op.swap.contexts else draft.posting.title
        await _save_owner_change(
            session, user_id, reviewed, summary=f"New phrasing of {bullet_id} for {context}"
        )
    return await _choose(session, draft, approve(draft.choices, bullet_id))


async def decide(
    session: AsyncSession,
    user_id: uuid.UUID,
    draft_id: uuid.UUID,
    skill: str,
    decision: SkillDecision | None,
) -> bool:
    """Confirm or drop a skill marked verify-before-shipping for this resume, or take the
    decision back (R3). Returns whether the new resume waits for a render."""
    draft = await _ready(session, user_id, draft_id)
    try:
        choices = decide_skill(draft.choices, draft.profile, skill, decision)
    except ChoiceError as error:
        raise DraftConflict(_sentence(str(error))) from None
    return await _choose(session, draft, choices)


async def log_application(
    session: AsyncSession,
    user_id: uuid.UUID,
    draft_id: uuid.UUID,
    *,
    applied_on: date,
    suffix: str,
) -> None:
    """Log the downloaded draft as an application, with its posting, the checks and the PDF
    (FR-TLR-8). Logging it again changes nothing. A resume that breaks a rule is logged with its
    errors (R2); one with a skill to confirm or drop isn't logged yet (R3)."""
    row = await _locked(session, user_id, draft_id)
    if row.application_id is not None:
        return
    draft = await _ready(session, user_id, draft_id, row=row)
    resume = draft.resume
    if resume.undecided:
        raise DraftConflict(_undecided(resume.undecided))
    if row.render_status != "rendered" or row.render_key is None:
        raise DraftConflict("The PDF isn't rendered yet.")
    stored = await find_render(session, user_id, row.render_key)
    if stored is None:
        raise DraftConflict("The PDF isn't kept any more; change a choice to render it again.")
    rendered = _rebuild(resume.selection, stored)
    if rendered is None:
        raise DraftConflict("The PDF can't be read; change a choice to render it again.")
    application, checks = record(
        draft.posting,
        rendered.selection,
        used=resume.used,
        rules=resume.rules,
        posting_text=row.posting_text,
        pdf=rendered.pdf,
        text=rendered.text,
        applied_on=applied_on,
        suffix=suffix,
    )
    posting_id = await save_job_posting(session, user_id, row.posting_text, draft.posting)
    await save_application(
        session,
        user_id,
        application,
        posting_id=posting_id,
        verifier_report=checks.model_dump(mode="json"),
    )
    row.application_id = application.id


async def draft_pdf(
    session: AsyncSession, user_id: uuid.UUID, draft_id: uuid.UUID
) -> tuple[str, bytes] | None:
    """The PDF of the draft's resume as the owner's choices make it, with a file name for it, or
    None until it's rendered."""
    row = await session.scalar(
        select(TailorDraftRow).where(
            TailorDraftRow.user_id == user_id, TailorDraftRow.id == draft_id
        )
    )
    if row is None or row.render_status != "rendered" or row.render_key is None:
        return None
    stored = await find_render(session, user_id, row.render_key)
    if stored is None:
        return None
    return f"tailored-draft-{str(row.id)[:8]}.pdf", stored.pdf


# --- The jobs ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParseJob:
    user_id: uuid.UUID
    text: str
    """The posting as pasted."""


async def posting_to_parse(session: AsyncSession, draft_id: uuid.UUID) -> ParseJob | None:
    """The posting a draft waits to have parsed, or None when it doesn't wait."""
    row = await session.get(TailorDraftRow, draft_id)
    if row is None or row.status != "parsing":
        return None
    return ParseJob(user_id=row.user_id, text=row.posting_text)


async def record_parsed(session: AsyncSession, draft_id: uuid.UUID, parsed: ParsedPosting) -> bool:
    """Keep the parsed posting; the draft is then tailored. Returns False, changing nothing, when
    it doesn't wait for it any more."""
    row = await session.scalar(
        select(TailorDraftRow).where(TailorDraftRow.id == draft_id).with_for_update()
    )
    if row is None or row.status != "parsing":
        return False
    posting = DraftPosting(posting=parsed.posting, dropped=parsed.dropped)
    row.posting = posting.model_dump(mode="json")
    row.status = "tailoring"
    return True


@dataclass(frozen=True)
class TailoringInputs:
    user_id: uuid.UUID
    version: Version
    """The newest profile version, which the draft is tailored from."""
    posting: JobPosting
    facts: list[Fact]
    """Every fact, for the verifier: decrypted here, and never stored with the draft."""
    avoid: tuple[str, ...]
    """The owner's words to avoid."""


async def begin_tailoring(
    session: AsyncSession, draft_id: uuid.UUID, *, now: datetime
) -> TailoringInputs | None:
    """Mark the draft's tailoring started, and read what it's tailored from. Returns None when it
    doesn't wait to be tailored, or another try started it recently and may still be at work.

    Raises `TailoringInterrupted` when an earlier try started long ago and didn't finish.
    """
    row = await session.scalar(
        select(TailorDraftRow).where(TailorDraftRow.id == draft_id).with_for_update()
    )
    if row is None or row.status != "tailoring":
        return None
    if row.started_at is not None:
        if row.started_at >= now - UNFINISHED_AFTER:
            return None
        raise TailoringInterrupted(draft_id)
    posting = _posting(row)
    version = await latest_version(session, row.user_id)
    if posting is None or version is None:
        row.status = "failed"
        row.error = "There's no profile to tailor from yet."
        return None
    row.started_at = now
    return TailoringInputs(
        user_id=row.user_id,
        version=version,
        posting=posting.posting,
        facts=await list_facts(session, row.user_id),
        avoid=(await get_style_notes(session, row.user_id)).avoid,
    )


async def record_tailoring(
    session: AsyncSession, draft_id: uuid.UUID, version: int, tailoring: Tailoring
) -> bool:
    """Keep what the jobs chose and wrote: the draft is then ready for the owner, each swap
    worked in and each new phrasing waiting for approval. Returns whether its resume waits for a
    render; False, changing nothing, when the draft doesn't wait to be tailored any more."""
    row = await session.scalar(
        select(TailorDraftRow).where(TailorDraftRow.id == draft_id).with_for_update()
    )
    posting = None if row is None else _posting(row)
    if row is None or row.status != "tailoring" or posting is None:
        return False
    row.status = "ready"
    row.profile_version = version
    row.tailoring = tailoring.model_dump(mode="json")
    row.choices = first_choices(tailoring).model_dump(mode="json")
    profile = (await get_version(session, row.user_id, version)).profile
    resume = draft_resume(profile, posting.posting, tailoring, first_choices(tailoring))
    return await _point_render(session, row, posting.posting, resume)


async def fail_draft(session: AsyncSession, draft_id: uuid.UUID, reason: str) -> None:
    """Mark a draft that's being parsed or tailored failed, saying why."""
    row = await session.scalar(
        select(TailorDraftRow).where(TailorDraftRow.id == draft_id).with_for_update()
    )
    if row is None or row.status not in ("parsing", "tailoring"):
        return
    row.status = "failed"
    row.error = reason


@dataclass(frozen=True)
class RenderJob:
    draft_id: uuid.UUID
    user_id: uuid.UUID
    selection: Selection
    """The resume the owner's choices make, before it's fitted to one page."""
    choices: dict[str, object]
    """The choices it was made from: the render is recorded only if they haven't changed."""
    posting: JobPosting
    stored: StoredRender | None
    """A render kept under its key, to use instead of compiling."""


async def draft_to_render(session: AsyncSession, draft_id: uuid.UUID) -> RenderJob | None:
    """The resume a draft waits to have rendered, or None when it doesn't wait."""
    row = await session.get(TailorDraftRow, draft_id)
    if row is None or row.render_status != "waiting":
        return None
    try:
        draft = await _draft(session, row)
    except DraftConflict:
        return None
    selection = draft.resume.selection
    try:
        stored = await find_render(session, row.user_id, render_key(selection))
    except RenderError:
        stored = None
    return RenderJob(
        draft_id=row.id,
        user_id=row.user_id,
        selection=selection,
        choices=dict(row.choices),
        posting=draft.posting,
        stored=stored,
    )


async def record_render(
    session: AsyncSession,
    job: RenderJob,
    rendered: RenderedResume | None,
    *,
    error: str | None = None,
) -> bool:
    """Keep a draft's render, for any resume with the same LaTeX source too, and give it to the
    draft, or mark the draft's render failed with `error` when it couldn't be rendered (None).
    Returns False, giving the draft nothing, when its choices changed meanwhile: another render
    follows them."""
    if rendered is not None:
        await save_render(session, job.user_id, rendered)
    row = await session.scalar(
        select(TailorDraftRow).where(TailorDraftRow.id == job.draft_id).with_for_update()
    )
    if row is None or row.render_status != "waiting" or row.choices != job.choices:
        return False
    if rendered is None:
        row.render_status = "failed"
        row.render_error = error or "It couldn't be rendered."
    else:
        _keep_render(row, rendered, job.posting)
    return True


def render_failure(error: CompileError | RenderError | FitError) -> str:
    """Why a draft's render failed, for the owner. LaTeX's first error line can quote the
    resume, so this is shown to the owner and never logged."""
    if isinstance(error, FitError):
        return "It doesn't fit one page even after cutting every bullet that can be cut."
    return _sentence(str(error))


def _sentence(text: str) -> str:
    """`text` as a sentence: a capital first letter and a full stop."""
    text = text.strip().rstrip(".")
    return f"{text[:1].upper()}{text[1:]}."


@dataclass(frozen=True)
class WaitingDrafts:
    parsing: list[uuid.UUID]
    tailoring: list[uuid.UUID]
    rendering: list[uuid.UUID]


async def waiting_drafts(session: AsyncSession) -> WaitingDrafts:
    """The drafts that wait for a job, oldest first: to parse their posting, to be tailored
    (including any whose try may have stopped, so it's failed), or to be rendered."""
    rows = await session.execute(
        select(TailorDraftRow.id, TailorDraftRow.status, TailorDraftRow.render_status)
        .where(
            TailorDraftRow.status.in_(("parsing", "tailoring"))
            | (TailorDraftRow.render_status == "waiting")
        )
        .order_by(TailorDraftRow.created_at)
    )
    waiting = WaitingDrafts(parsing=[], tailoring=[], rendering=[])
    for draft_id, status, render_status in rows:
        if status == "parsing":
            waiting.parsing.append(draft_id)
        elif status == "tailoring":
            waiting.tailoring.append(draft_id)
        elif render_status == "waiting":
            waiting.rendering.append(draft_id)
    return waiting


async def next_step(
    session: AsyncSession, draft_id: uuid.UUID
) -> Literal["parse", "tailor", "render"] | None:
    """The job a draft waits for, if any."""
    row = await session.get(TailorDraftRow, draft_id)
    if row is None:
        return None
    if row.status == "parsing":
        return "parse"
    if row.status == "tailoring":
        return "tailor"
    if row.render_status == "waiting":
        return "render"
    return None


# --- Helpers -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Draft:
    """A ready draft, read back."""

    row: TailorDraftRow
    profile: Profile
    """The profile version it was tailored from."""
    posting: JobPosting
    tailoring: Tailoring
    choices: DraftChoices
    resume: DraftResume


async def _locked(session: AsyncSession, user_id: uuid.UUID, draft_id: uuid.UUID) -> TailorDraftRow:
    row = await session.scalar(
        select(TailorDraftRow)
        .where(TailorDraftRow.user_id == user_id, TailorDraftRow.id == draft_id)
        .with_for_update()
    )
    if row is None:
        raise DraftNotFound(draft_id)
    return row


async def _ready(
    session: AsyncSession,
    user_id: uuid.UUID,
    draft_id: uuid.UUID,
    *,
    row: TailorDraftRow | None = None,
) -> _Draft:
    """The draft, locked, once it's ready for the owner's choices and not yet logged."""
    row = row or await _locked(session, user_id, draft_id)
    if row.status != "ready":
        raise DraftConflict("It isn't tailored yet.")
    if row.application_id is not None:
        raise DraftConflict("It's logged as an application, so it can't change.")
    return await _draft(session, row)


async def _draft(session: AsyncSession, row: TailorDraftRow) -> _Draft:
    posting = _posting(row)
    if row.tailoring is None or posting is None or row.profile_version is None:
        raise DraftConflict("It isn't tailored yet.")
    try:
        profile = (await get_version(session, row.user_id, row.profile_version)).profile
    except VersionError:
        raise DraftConflict(_version_gone(row.profile_version)) from None
    tailoring = Tailoring.model_validate(row.tailoring)
    choices = DraftChoices.model_validate(row.choices)
    return _Draft(
        row=row,
        profile=profile,
        posting=posting.posting,
        tailoring=tailoring,
        choices=choices,
        resume=draft_resume(profile, posting.posting, tailoring, choices),
    )


async def _choose(session: AsyncSession, draft: _Draft, choices: DraftChoices) -> bool:
    """Keep the owner's new choices, and point the render at the resume they make."""
    row = draft.row
    row.choices = choices.model_dump(mode="json")
    resume = draft_resume(draft.profile, draft.posting, draft.tailoring, choices)
    return await _point_render(session, row, draft.posting, resume)


async def _point_render(
    session: AsyncSession, row: TailorDraftRow, posting: JobPosting, resume: DraftResume
) -> bool:
    """Point the draft's render at `resume`. A render kept under its key is used at once, read
    back without LaTeX; otherwise the draft waits for one. Returns whether it waits."""
    try:
        key = render_key(resume.selection)
    except RenderError as error:
        row.render_status, row.render_key = "failed", None
        row.pdf_sha256, row.render, row.render_error = None, None, render_failure(error)
        return False
    if row.render_key == key and row.render_status in ("rendered", "failed"):
        return False
    row.render_key = key
    stored = await find_render(session, row.user_id, key)
    rendered = None if stored is None else _rebuild(resume.selection, stored)
    if rendered is not None:
        _keep_render(row, rendered, posting)
        return False
    row.render_status = "waiting"
    row.pdf_sha256, row.render, row.render_error = None, None, None
    return True


def _rebuild(selection: Selection, stored: StoredRender) -> RenderedResume | None:
    """A kept render of `selection`, read back without LaTeX, or None if it can't be read."""
    try:
        return render_resume(selection, stored=stored)
    except (RenderError, PyPdfError):
        return None


def _keep_render(row: TailorDraftRow, rendered: RenderedResume, posting: JobPosting) -> None:
    report, ats = pdf_checks(rendered.selection, posting, rendered.text)
    checks = RenderChecks(
        pages=rendered.pages,
        cuts=tuple(ResumeCut(kind=cut.kind, id=cut.id, text=cut.text) for cut in rendered.cuts),
        warnings=rendered.warnings,
        coverage=report,
        ats=ats,
    )
    row.render_status = "rendered"
    row.render_key = rendered.key
    row.pdf_sha256 = hashlib.sha256(rendered.pdf).hexdigest()
    row.render = checks.model_dump(mode="json")
    row.render_error = None


async def _save_owner_change(
    session: AsyncSession, user_id: uuid.UUID, reviewed: ReviewedOp, *, summary: str
) -> int:
    """Apply one change the owner made to the newest profile version, as a change set by the
    owner, accepted and applied at once, and save the result as the next version."""
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())
    latest = await latest_version(session, user_id)
    if latest is None:
        raise DraftConflict("There's no profile to save the swap to.")
    try:
        profile = profile_ops.apply(latest.profile, [reviewed.op])
    except profile_ops.ProfileOpError as error:
        raise DraftConflict(f"It can't be saved as a swap: {error}.") from None
    change_set = ChangeSet(
        author="owner", ops=(reviewed,), summary=summary, base_version=latest.number
    )
    change_set_id = await propose_change_set(session, user_id, change_set)
    version = await save_version(
        session, user_id, profile, author="owner", change_set_id=change_set_id
    )
    await close_change_set(session, user_id, change_set_id, "applied")
    return version.number


async def _view(session: AsyncSession, row: TailorDraftRow) -> TailorDraft:
    posting = _posting(row)
    base = TailorDraft(
        id=row.id,
        created_at=row.created_at,
        status=row.status,
        error=row.error,
        posting=posting.posting if posting else None,
        dropped_terms=posting.dropped if posting else (),
        profile_version=row.profile_version,
    )
    if row.status != "ready":
        blocked = "Tailoring failed." if row.status == "failed" else "It isn't tailored yet."
        return base.model_copy(update={"blocked": (blocked,)})
    try:
        draft = await _draft(session, row)
    except DraftConflict as error:
        return base.model_copy(update={"error": str(error), "blocked": (str(error),)})
    resume = draft.resume
    checks = RenderChecks.model_validate(row.render) if row.render else None
    fitted = row.render_status == "rendered" and checks is not None
    render = None
    if row.render_status is not None:
        render = DraftRender(
            status=row.render_status,
            pdf=_pdf(row, checks),
            ats=checks.ats if checks else None,
            error=row.render_error,
        )
    application = await _application(session, row)
    return base.model_copy(
        update={
            "role_type": resume.role_type,
            "notes": resume.notes,
            "bullets": tuple(
                DraftWords(
                    id=bullet.id,
                    under=bullet.under,
                    original=bullet.original,
                    use=bullet.use,
                    swap=bullet.swap.report if bullet.swap else None,
                    phrasing=bullet.phrasing.report if bullet.phrasing else None,
                    approved=bullet.approved,
                )
                for bullet in resume.bullets
            ),
            "skills": tuple(
                DraftSkill(name=skill.name, decision=skill.decision, locations=skill.locations)
                for skill in resume.skills
            ),
            "outline": _outline(resume.selection),
            "rules": resume.rules,
            "coverage": checks.coverage
            if fitted and checks
            else coverage(resume.selection, draft.posting),
            "coverage_fitted": fitted,
            "render": render,
            "blocked": () if application else _blocked(row, resume),
            "application": application,
        }
    )


def _blocked(row: TailorDraftRow, resume: DraftResume) -> tuple[str, ...]:
    blocked: list[str] = []
    if resume.undecided:
        blocked.append(_undecided(resume.undecided))
    if row.render_status == "waiting":
        blocked.append("The PDF isn't rendered yet.")
    elif row.render_status == "failed":
        blocked.append("The PDF couldn't be rendered.")
    return tuple(blocked)


def _undecided(skills: tuple[str, ...]) -> str:
    names = ", ".join(skills)
    return f"Confirm or drop each skill marked verify-before-shipping: {names}."


def _version_gone(version: int) -> str:
    return f"Profile version {version}, which it was tailored from, is gone; tailor it again."


def _pdf(row: TailorDraftRow, checks: RenderChecks | None) -> ResumePdf | None:
    if row.render_status != "rendered" or row.pdf_sha256 is None or checks is None:
        return None
    return ResumePdf(
        sha256=row.pdf_sha256, pages=checks.pages, cuts=checks.cuts, warnings=checks.warnings
    )


async def _application(session: AsyncSession, row: TailorDraftRow) -> DraftApplication | None:
    if row.application_id is None:
        return None
    found = (
        await session.execute(
            select(
                ApplicationRow.applied_on, ApplicationRow.pdf_sha256, ApplicationRow.verifier_report
            ).where(ApplicationRow.user_id == row.user_id, ApplicationRow.id == row.application_id)
        )
    ).one_or_none()
    if found is None or found.pdf_sha256 is None:
        return None
    rules = (
        None
        if found.verifier_report is None
        else ResumeChecks.model_validate(found.verifier_report).rules
    )
    return DraftApplication(
        id=row.application_id,
        applied_on=found.applied_on,
        pdf_sha256=found.pdf_sha256,
        sendable=rules is not None and rules.ok,
    )


def _outline(selection: Selection) -> DraftOutline:
    return DraftOutline(
        summary=selection.summary,
        work=tuple(
            OutlineItem(
                id=role.id,
                name=role.organisation,
                title=role.title,
                bullets=tuple(OutlineBullet(id=b.id, text=b.text) for b in role.bullets),
            )
            for role in selection.work
        ),
        projects=tuple(
            OutlineItem(
                id=project.id,
                name=project.name,
                bullets=tuple(OutlineBullet(id=b.id, text=b.text) for b in project.bullets),
            )
            for project in selection.projects
        ),
        skills=selection.skills,
    )


def _posting(row: TailorDraftRow) -> DraftPosting | None:
    return None if row.posting is None else DraftPosting.model_validate(row.posting)
