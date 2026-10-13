"""`/api/inbox`: review the changes proposed to the signed-in owner's profile (T-040)."""

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import StringConstraints

from app.auth.routes import NO_STORE
from app.auth.sessions import CurrentUser
from app.deps import Db, Llm
from app.inbox.review import (
    Inbox,
    InboxChangeSet,
    NotFound,
    ReviewError,
    accept_passed,
    apply_change_set,
    decide,
    edit_text,
    load_change_set,
    load_inbox,
)
from app.llm.client import LLMConfigError, LLMError
from app.llm.usage import DatabaseCallLog
from app.schema.changes import OpStatus
from app.schema.common import Model

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

REASON_LIMIT = 500
"""The longest reason for a rejection, in characters. It becomes a style note (FR-REV-4)."""
BULLET_LIMIT = 500
"""The longest bullet text, in characters: well past the two lines a bullet may take."""


class Decision(Model):
    status: OpStatus
    """`accepted`, `rejected`, or `proposed` to undo a decision."""
    reason: Annotated[str, StringConstraints(max_length=REASON_LIMIT)] | None = None
    """Why it's rejected. It's kept only for a rejection."""


class BulletText(Model):
    text: Annotated[str, StringConstraints(min_length=1, max_length=BULLET_LIMIT)]


class Applied(Model):
    version: int | None
    """The new profile version; None when nothing was accepted."""


@router.get("")
async def inbox(user: CurrentUser, db: Db, response: Response) -> Inbox:
    """The change sets waiting for review, oldest first (FR-REV-1)."""
    response.headers.update(NO_STORE)
    return await load_inbox(db, user.id)


@router.post("/{change_set_id}/ops/{position}/decision")
async def decide_change(
    change_set_id: uuid.UUID,
    position: int,
    body: Decision,
    user: CurrentUser,
    db: Db,
    response: Response,
) -> InboxChangeSet:
    """Accept or reject one change, or undo the decision (FR-REV-2)."""
    response.headers.update(NO_STORE)
    reason = body.reason or None
    with _errors():
        await decide(db, user.id, change_set_id, position, body.status, reason)
        change_set = await load_change_set(db, user.id, change_set_id)
    await db.commit()
    return change_set


@router.put("/{change_set_id}/ops/{position}/text")
async def edit_change(
    change_set_id: uuid.UUID,
    position: int,
    body: BulletText,
    user: CurrentUser,
    db: Db,
    llm: Llm,
    response: Response,
) -> InboxChangeSet:
    """Edit the text of a new or reworded bullet. The verifier checks it again, and it waits for
    a decision once more (FR-REV-2, FR-FID-7). Claude unavailable: 502, or 503 when it isn't
    set up."""
    response.headers.update(NO_STORE)
    log = DatabaseCallLog(user.id)
    try:
        with _errors():
            await edit_text(db, user.id, change_set_id, position, body.text, llm(log))
            change_set = await load_change_set(db, user.id, change_set_id)
    except LLMConfigError:
        await _keep_calls(db, log)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Claude isn't set up to check the text"
        ) from None
    except LLMError:
        await _keep_calls(db, log)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Claude couldn't check the text; try again"
        ) from None
    await log.save(db)
    await db.commit()
    return change_set


@router.post("/{change_set_id}/accept-passed")
async def accept_all(
    change_set_id: uuid.UUID, user: CurrentUser, db: Db, response: Response
) -> InboxChangeSet:
    """Accept every change still waiting whose text passed the verifier (bulk accept). Flagged
    ones keep waiting for a decision of their own."""
    response.headers.update(NO_STORE)
    with _errors():
        await accept_passed(db, user.id, change_set_id)
        change_set = await load_change_set(db, user.id, change_set_id)
    await db.commit()
    return change_set


@router.post("/{change_set_id}/apply")
async def apply_changes(
    change_set_id: uuid.UUID, user: CurrentUser, db: Db, response: Response
) -> Applied:
    """Apply the accepted changes as one new profile version (FR-REV-3), once every change is
    decided (409 before). Reasons given for rejections become style notes (FR-REV-4)."""
    response.headers.update(NO_STORE)
    with _errors():
        version = await apply_change_set(db, user.id, change_set_id, now=datetime.now(UTC))
    await db.commit()
    return Applied(version=version)


async def _keep_calls(db: Db, log: DatabaseCallLog) -> None:
    """Save the calls Claude was asked to make, though the edit didn't happen."""
    await db.rollback()
    await log.save(db)
    await db.commit()


@contextmanager
def _errors() -> Generator[None]:
    try:
        yield
    except NotFound as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from None
    except ReviewError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
