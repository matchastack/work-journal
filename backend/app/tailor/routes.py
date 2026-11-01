"""`/api/tailor`: resumes tailored to pasted job postings, and the application history (T-045).

The API never runs LaTeX or a long model call inside a request (C6, NFR-PERF-1): a request
stores what changed and answers, then runs the draft's next job, if it has one (`draft_now`).
The page reads the draft again while a job runs. Responses aren't stored by the browser
(`no-store`): a draft and its PDF are personal data.
"""

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Path, Response, status
from pydantic import StringConstraints

from app.auth.routes import NO_STORE
from app.auth.sessions import CurrentUser
from app.db.store import PDF
from app.deps import Db
from app.resumes.routes import PDF_HEADERS
from app.schema.common import Model
from app.tailor.drafts import (
    DraftConflict,
    DraftNotFound,
    approve_phrasing,
    choose_words,
    create_draft,
    decide,
    discard_draft,
    draft_pdf,
    load_draft,
    load_page,
    log_application,
    retry_draft,
)
from app.tailor.view import TailorDraft, TailorPage
from app.tailoring.drafts import BulletUse, SkillDecision
from app.tick import draft_now

router = APIRouter(prefix="/api/tailor", tags=["tailor"])

POSTING_LIMIT = 50_000
"""The longest posting, in characters: several times a long one."""
SKILL_LIMIT = 100

BulletId = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=64)]


class NewDraft(Model):
    text: Annotated[str, StringConstraints(min_length=1, max_length=POSTING_LIMIT)]
    """The job posting, as pasted."""


class Words(Model):
    use: BulletUse
    """`own` words, the `swap` worked in, or the approved new `phrasing`."""


class SkillChoice(Model):
    skill: Annotated[str, StringConstraints(min_length=1, max_length=SKILL_LIMIT)]
    decision: SkillDecision | None
    """`confirmed`, `dropped`, or None to take the decision back."""


class Download(Model):
    applied_on: date | None = None
    """The owner's date today, which the application is logged on; the server's by default. It
    may differ from the server's by a day, as time zones do."""


@router.get("")
async def tailor_page(user: CurrentUser, db: Db, response: Response) -> TailorPage:
    """The drafts not yet downloaded, newest first, and the application history (FR-TLR-8)."""
    response.headers.update(NO_STORE)
    return await load_page(db, user.id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def new_draft(
    body: NewDraft, user: CurrentUser, db: Db, response: Response, background: BackgroundTasks
) -> TailorDraft:
    """Tailor a resume to a pasted posting. Jobs parse the posting, then tailor the resume, and
    the draft says how far they've got."""
    response.headers.update(NO_STORE)
    draft_id = await create_draft(db, user.id, body.text)
    draft = await load_draft(db, user.id, draft_id)
    await db.commit()
    background.add_task(draft_now, draft_id)
    return draft


@router.get("/{draft_id}")
async def draft(draft_id: uuid.UUID, user: CurrentUser, db: Db, response: Response) -> TailorDraft:
    """A draft: the parsed posting, then the tailored resume with its coverage report, the
    verifier's and the resume rules' findings, its render and the ATS check."""
    response.headers.update(NO_STORE)
    with _errors():
        return await load_draft(db, user.id, draft_id)


@router.delete("/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard(draft_id: uuid.UUID, user: CurrentUser, db: Db) -> Response:
    """Discard a draft not yet downloaded. A swap saved from it stays in the profile."""
    with _errors():
        await discard_draft(db, user.id, draft_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=NO_STORE)


@router.post("/{draft_id}/retry")
async def retry(
    draft_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    response: Response,
    background: BackgroundTasks,
) -> TailorDraft:
    """Try a failed draft again."""
    response.headers.update(NO_STORE)
    with _errors():
        await retry_draft(db, user.id, draft_id)
        draft = await load_draft(db, user.id, draft_id)
    await db.commit()
    background.add_task(draft_now, draft_id)
    return draft


@router.put("/{draft_id}/bullets/{bullet_id}")
async def choose(
    draft_id: uuid.UUID,
    bullet_id: BulletId,
    body: Words,
    user: CurrentUser,
    db: Db,
    response: Response,
    background: BackgroundTasks,
) -> TailorDraft:
    """Choose a reworded bullet's words (FR-TLR-3). A render of the new choices kept before is
    used at once; otherwise the resume is rendered again."""
    response.headers.update(NO_STORE)
    with _errors():
        render = await choose_words(db, user.id, draft_id, bullet_id, body.use)
        draft = await load_draft(db, user.id, draft_id)
    await db.commit()
    if render:
        background.add_task(draft_now, draft_id)
    return draft


@router.post("/{draft_id}/bullets/{bullet_id}/approve")
async def approve(
    draft_id: uuid.UUID,
    bullet_id: BulletId,
    user: CurrentUser,
    db: Db,
    response: Response,
    background: BackgroundTasks,
) -> TailorDraft:
    """Approve a bullet's new phrasing, which passed the verifier: it's saved as a swap on the
    bullet, in your profile's next version, and the draft uses it (FR-WRT-5)."""
    response.headers.update(NO_STORE)
    with _errors():
        render = await approve_phrasing(db, user.id, draft_id, bullet_id)
        draft = await load_draft(db, user.id, draft_id)
    await db.commit()
    if render:
        background.add_task(draft_now, draft_id)
    return draft


@router.put("/{draft_id}/skills")
async def decide_skill(
    draft_id: uuid.UUID,
    body: SkillChoice,
    user: CurrentUser,
    db: Db,
    response: Response,
    background: BackgroundTasks,
) -> TailorDraft:
    """Confirm a skill marked verify-before-shipping for this resume, drop it, or take the
    decision back (R3)."""
    response.headers.update(NO_STORE)
    with _errors():
        render = await decide(db, user.id, draft_id, body.skill, body.decision)
        draft = await load_draft(db, user.id, draft_id)
    await db.commit()
    if render:
        background.add_task(draft_now, draft_id)
    return draft


@router.get(
    "/{draft_id}/pdf",
    response_class=Response,
    responses={200: {"content": {PDF: {"schema": {"type": "string", "format": "binary"}}}}},
)
async def pdf(draft_id: uuid.UUID, user: CurrentUser, db: Db) -> Response:
    """The PDF of the draft's resume as your choices make it, to preview. 404 until it's
    rendered. Downloading it logs the application, so that's `POST .../application` instead."""
    found = await draft_pdf(db, user.id, draft_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The draft has no PDF yet")
    name, data = found
    headers = PDF_HEADERS | {"Content-Disposition": f'inline; filename="{name}"'}
    return Response(data, media_type=PDF, headers=headers)


@router.post("/{draft_id}/application")
async def download(
    draft_id: uuid.UUID, body: Download, user: CurrentUser, db: Db, response: Response
) -> TailorDraft:
    """Log the draft as an application, once (FR-TLR-8), so its PDF can be downloaded from
    `/api/resumes/pdf/{sha256}`. 409 while a skill marked verify-before-shipping waits for a
    decision (R3) or the PDF isn't rendered. A resume that breaks a rule is logged with its
    errors, and marked not to send (R2)."""
    response.headers.update(NO_STORE)
    today = datetime.now(UTC).date()
    applied_on = body.applied_on or today
    if abs(applied_on - today) > timedelta(days=1):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That date isn't today")
    with _errors():
        await log_application(
            db, user.id, draft_id, applied_on=applied_on, suffix=uuid.uuid4().hex[:6]
        )
        draft = await load_draft(db, user.id, draft_id)
    await db.commit()
    return draft


@contextmanager
def _errors() -> Generator[None]:
    try:
        yield
    except DraftNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such draft") from None
    except DraftConflict as error:
        # The page adds its own full stop.
        raise HTTPException(status.HTTP_409_CONFLICT, str(error).rstrip(".")) from None
