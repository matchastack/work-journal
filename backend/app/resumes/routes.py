"""`/api/resumes`: the signed-in owner's master and tailored resumes, and their PDFs (T-044).

Responses aren't stored by the browser (`no-store`), as elsewhere in the API. A PDF's URL names
its hash, so it could be cached for good, but a resume is personal data: kept in the browser's
cache, it would outlive signing out on a shared computer. PDFs are small, and the page keeps one
URL per PDF, so a preview isn't fetched again while the list is refreshed.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Response, status

from app.auth.routes import NO_STORE
from app.auth.sessions import CurrentUser
from app.db.store import PDF
from app.deps import Db
from app.resumes.listing import Resumes, load_resumes, resume_pdf

router = APIRouter(prefix="/api/resumes", tags=["resumes"])

PDF_HEADERS = {
    **NO_STORE,
    "X-Content-Type-Options": "nosniff",
    # The page previews the PDF in a frame; no other site may.
    "Content-Security-Policy": "frame-ancestors 'self'",
}
Sha256 = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]


@router.get("")
async def resumes(user: CurrentUser, db: Db, response: Response) -> Resumes:
    """The master resume from the latest refresh (FR-RES-5, FR-RES-6), and every tailored
    resume, newest first, with each PDF's page count, cut report and rule findings."""
    response.headers.update(NO_STORE)
    return await load_resumes(db, user.id)


@router.get(
    "/pdf/{sha256}",
    response_class=Response,
    responses={200: {"content": {PDF: {"schema": {"type": "string", "format": "binary"}}}}},
)
async def pdf(sha256: Sha256, user: CurrentUser, db: Db, download: bool = False) -> Response:
    """A resume's PDF, to preview in the page (inline) or, with `download=true`, to save. 404
    when no resume of the owner's has it."""
    found = await resume_pdf(db, user.id, sha256)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No resume of yours has this PDF")
    name, data = found
    disposition = "attachment" if download else "inline"
    headers = PDF_HEADERS | {"Content-Disposition": f'{disposition}; filename="{name}"'}
    return Response(data, media_type=PDF, headers=headers)
