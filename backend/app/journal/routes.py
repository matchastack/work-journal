"""`/api/journal`: the signed-in owner's journal (T-041)."""

from fastapi import APIRouter, Response

from app.auth.routes import NO_STORE
from app.auth.sessions import CurrentUser
from app.deps import Db
from app.journal.document import Journal, load_journal

router = APIRouter(prefix="/api/journal", tags=["journal"])


@router.get("")
async def journal(user: CurrentUser, db: Db, response: Response) -> Journal:
    """Every entry, oldest first, with its messages and facts (FR-JRN-5)."""
    response.headers.update(NO_STORE)
    return await load_journal(db, user.id)
