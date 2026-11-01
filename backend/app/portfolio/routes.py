"""The portfolio page on the web (T-046): the public page at `/p/<handle>`, and `/api/portfolio`,
where the signed-in owner previews, publishes and unpublishes it and chooses its resumes.

The public routes read no cookie and no user: the page is the same for every visitor, so a CDN
may cache it (NFR-PERF-2). `Cache-Control` lets browsers keep it only if they check it first
(`max-age=0`, with an ETag of the page), and shared caches, such as Vercel's CDN, keep it for 60
s, then serve it stale for up to 60 s more while they fetch it again. So a change, including
unpublishing, shows everywhere within about two minutes, while most visitors get a cached copy.
A page that isn't found is never cached, so publishing shows at once (FR-PRT-3).

Unknown handles and unpublished pages get the same 404, so the page says nothing of whether a
handle exists. The routes are left out of the OpenAPI schema: they serve HTML and PDFs, not JSON.
"""

import hashlib
import re
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import AfterValidator, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.routes import NO_STORE
from app.auth.sessions import CurrentUser
from app.db.store import get_artifact
from app.deps import AppSettings, Db
from app.portfolio.build import TEMPLATE_DIR, Assets, Link, portfolio_page, render_page
from app.portfolio.publishing import (
    NotPublishable,
    Offered,
    PortfolioContent,
    PortfolioView,
    choose_resumes,
    find_published,
    offered_resumes,
    portfolio_view,
    preview_content,
    publish,
    published_content,
    unpublish,
)
from app.schema.common import Model
from app.schema.settings import HANDLE, HANDLE_LENGTH, MAX_RESUMES, PublishedResume

PDF = "application/pdf"
PUBLIC_CACHE = "public, max-age=0, s-maxage=60, stale-while-revalidate=60"
"""Browsers check each time (cheaply, with the ETag); shared caches keep it about a minute."""
ASSET_CACHE = "public, max-age=31536000, immutable"
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors {ancestors}"
)
PUBLIC_CSP = _CSP.format(ancestors="'none'")
PREVIEW_CSP = _CSP.format(ancestors="'self'")
"""The preview shows in a frame on the Portfolio page."""
NOT_FOUND_HEADERS = {
    **NO_STORE,
    "Content-Security-Policy": "default-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex",
}
NOT_FOUND_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex">
  <title>Page not found</title>
</head>
<body>
  <h1>Page not found</h1>
  <p>There's no page at this address.</p>
</body>
</html>
"""
ASSET_TYPES = {"portfolio.css": "text/css", "portfolio.js": "text/javascript"}
ASSET_VERSIONS = {
    name: hashlib.sha256((TEMPLATE_DIR / "static" / name).read_bytes()).hexdigest()[:12]
    for name in ASSET_TYPES
}
"""Each asset's version, in its address, so a changed file gets a new one and can be cached
for a year."""
SERVED_ASSETS = Assets(
    stylesheet=f"/p/_assets/portfolio.css?v={ASSET_VERSIONS['portfolio.css']}",
    script=f"/p/_assets/portfolio.js?v={ASSET_VERSIONS['portfolio.js']}",
)
_PDF_NAME = re.compile(r"([0-9a-f]{64})\.pdf")

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])
public_router = APIRouter(prefix="/p", include_in_schema=False)


def _each_once(resumes: tuple[PublishedResume, ...]) -> tuple[PublishedResume, ...]:
    keys = [resume.key for resume in resumes]
    if len(set(keys)) != len(keys):
        raise ValueError("each resume can be offered once")
    return resumes


class ResumesChange(Model):
    """The resumes the page offers, in order (FR-PRT-7)."""

    resumes: Annotated[
        tuple[PublishedResume, ...], Field(max_length=MAX_RESUMES), AfterValidator(_each_once)
    ]


# --- The owner's Portfolio page ------------------------------------------------------------------


@router.get("")
async def portfolio(user: CurrentUser, db: Db, response: Response) -> PortfolioView:
    """Whether the page is published, the version it shows, its settings and its resumes."""
    response.headers.update(NO_STORE)
    return await portfolio_view(db, user.id)


@router.post("/publish")
async def publish_page(user: CurrentUser, db: Db, response: Response) -> PortfolioView:
    """Publish the page, at once (FR-PRT-3). Refused (409) without a handle or a profile."""
    response.headers.update(NO_STORE)
    try:
        await publish(db, user.id, now=datetime.now(UTC))
    except NotPublishable as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    await db.commit()
    return await portfolio_view(db, user.id)


@router.post("/unpublish")
async def unpublish_page(user: CurrentUser, db: Db, response: Response) -> PortfolioView:
    """Take the page down. Caches may show it for up to about two minutes more."""
    response.headers.update(NO_STORE)
    await unpublish(db, user.id)
    await db.commit()
    return await portfolio_view(db, user.id)


@router.put("/resumes")
async def change_resumes(
    body: ResumesChange, user: CurrentUser, db: Db, response: Response
) -> PortfolioView:
    """Replace the resumes visitors may download (FR-PRT-7). One that can't be offered, such as
    a tailored resume with an error, is refused (409)."""
    response.headers.update(NO_STORE)
    try:
        await choose_resumes(db, user.id, body.resumes)
    except NotPublishable as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    await db.commit()
    return await portfolio_view(db, user.id)


@router.get("/preview", include_in_schema=False)
async def preview(user: CurrentUser, db: Db, settings: AppSettings) -> HTMLResponse:
    """The page as it's published, or as publishing it now would show it."""
    content = await preview_content(db, user.id)
    if content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no profile to show yet")
    site_url = None if content.handle is None else f"{settings.app_url}/p/{content.handle}"
    html = page_html(
        content,
        site_url=site_url,
        resume_href=lambda sha: f"/api/portfolio/preview/resumes/{sha}.pdf",
    )
    headers = {
        **NO_STORE,
        "Content-Security-Policy": PREVIEW_CSP,
        "X-Content-Type-Options": "nosniff",
        "X-Robots-Tag": "noindex",
    }
    return HTMLResponse(html, headers=headers)


@router.get("/preview/resumes/{name}", include_in_schema=False)
async def preview_resume(name: str, user: CurrentUser, db: Db) -> Response:
    """A resume the owner chose for the page, published or not."""
    content = await preview_content(db, user.id)
    match = _PDF_NAME.fullmatch(name)
    if content is None or match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such resume")
    response = await _resume(db, user.id, content.resumes, match[1], content.handle or "resume")
    if response is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such resume")
    response.headers.update(NO_STORE)
    return response


# --- The public page -----------------------------------------------------------------------------


@public_router.get("/_assets/{name}")
async def asset(name: str, v: Annotated[str | None, Query()] = None) -> Response:
    """The page's CSS and JavaScript."""
    media_type = ASSET_TYPES.get(name)
    if media_type is None:
        return not_found()
    cache = ASSET_CACHE if v == ASSET_VERSIONS[name] else "public, max-age=300"
    return FileResponse(
        TEMPLATE_DIR / "static" / name,
        media_type=media_type,
        headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"},
    )


@public_router.api_route("/{handle}", methods=["GET", "HEAD"])
async def public_page(handle: str, request: Request, db: Db, settings: AppSettings) -> Response:
    """The published portfolio page (FR-PRT-1). Not found unless it's published."""
    if not valid_handle(handle.lower()):
        return not_found()
    if handle != handle.lower():
        return RedirectResponse(f"/p/{handle.lower()}", status.HTTP_308_PERMANENT_REDIRECT)
    published = await find_published(db, handle)
    content = None if published is None else await published_content(db, published)
    if content is None:
        return not_found()
    html = page_html(
        content,
        site_url=f"{settings.app_url}/p/{handle}",
        resume_href=lambda sha: f"/p/{handle}/resumes/{sha}.pdf",
    )
    etag = f'"{hashlib.sha256(html.encode()).hexdigest()[:32]}"'
    headers = _public_headers(content.settings.indexing) | {
        "ETag": etag,
        "Content-Security-Policy": PUBLIC_CSP,
    }
    if _matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return HTMLResponse(html, headers=headers)


@public_router.get("/{handle}/resumes/{name}")
async def public_resume(handle: str, name: str, request: Request, db: Db) -> Response:
    """A resume the owner chose to offer on the published page (FR-PRT-7). Only those PDFs are
    served, and only while the page is published."""
    match = _PDF_NAME.fullmatch(name)
    if match is None or not valid_handle(handle):
        return not_found()
    published = await find_published(db, handle)
    if published is None:
        return not_found()
    offered = await offered_resumes(db, published.user_id, published.publishing.resumes)
    if _matches(request.headers.get("if-none-match"), f'"{match[1]}"') and any(
        resume.sha256 == match[1] for resume in offered
    ):
        headers = _public_headers(published.settings.indexing) | {"ETag": f'"{match[1]}"'}
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    response = await _resume(db, published.user_id, offered, match[1], handle)
    if response is None:
        return not_found()
    response.headers.update(_public_headers(published.settings.indexing))
    return response


@public_router.get("/{rest:path}")
async def no_page(rest: str) -> Response:
    """Anything else under `/p/`: a handle with a slash after it goes to the page, and the rest
    isn't found, rather than reaching the web app."""
    handle = rest.rstrip("/").lower()
    if rest.endswith("/") and valid_handle(handle):
        return RedirectResponse(f"/p/{handle}", status.HTTP_308_PERMANENT_REDIRECT)
    return not_found()


# --- Helpers -------------------------------------------------------------------------------------


def page_html(
    content: PortfolioContent, *, site_url: str | None, resume_href: Callable[[str], str]
) -> str:
    """The page, from the version and settings in `content`."""
    settings = content.settings
    page = portfolio_page(
        content.profile,
        year=date.today().year,
        site_url=site_url,
        downloads=[
            Link(text=resume.label, href=resume_href(resume.sha256)) for resume in content.resumes
        ],
        default_visibility=settings.default_visibility,
        open_to_work=settings.open_to_work,
        indexing=settings.indexing,
        assets=SERVED_ASSETS,
    )
    return render_page(page)


def valid_handle(handle: str) -> bool:
    shortest, longest = HANDLE_LENGTH
    return shortest <= len(handle) <= longest and HANDLE.fullmatch(handle) is not None


def not_found() -> HTMLResponse:
    return HTMLResponse(NOT_FOUND_PAGE, status.HTTP_404_NOT_FOUND, headers=NOT_FOUND_HEADERS)


def _public_headers(indexing: bool) -> dict[str, str]:
    headers = {"Cache-Control": PUBLIC_CACHE, "X-Content-Type-Options": "nosniff"}
    if not indexing:
        headers["X-Robots-Tag"] = "noindex"
    return headers


async def _resume(
    db: AsyncSession, user_id: uuid.UUID, offered: Sequence[Offered], sha256: str, handle: str
) -> Response | None:
    """The offered resume whose PDF has the hash `sha256`, as a download; None if it isn't
    offered."""
    resume = next((resume for resume in offered if resume.sha256 == sha256), None)
    if resume is None:
        return None
    stored = await get_artifact(db, user_id, sha256)
    if stored is None or stored[0] != PDF:
        return None
    headers = {
        "Content-Disposition": f'attachment; filename="{_file_name(handle, resume.label)}"',
        "ETag": f'"{sha256}"',
        "X-Content-Type-Options": "nosniff",
    }
    return Response(stored[1], media_type=PDF, headers=headers)


def _matches(if_none_match: str | None, etag: str) -> bool:
    """Whether the browser's copy is current (`If-None-Match`)."""
    if not if_none_match:
        return False
    tags = {tag.strip().removeprefix("W/") for tag in if_none_match.split(",")}
    return "*" in tags or etag in tags


def _file_name(handle: str, label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "resume"
    return f"{handle}-{slug}.pdf"
