"""Publishing the portfolio page (FR-PRT-1, FR-PRT-3, FR-PRT-7): which version it shows, which
resumes it offers, and whose page a handle names.

- **The version.** A published page shows the profile version from the latest refresh
  (FR-REV-7). Publishing pins the version the outputs show then (`outputs_version`): the latest
  refresh's, or the newest version before the first refresh. A later refresh's version replaces
  it as soon as that refresh's outputs fall due (`shown_version`), so versions made since, such as
  edits waiting for the next refresh, never show (drafts, FR-PRT-3).
- **The resumes.** Visitors may download the resumes the owner chooses: the master resume the
  latest refresh rendered, and tailored resumes that passed their checks, since one with an error
  mustn't be sent (R2). Only their stored PDFs (`artifacts`) are served.
- **The page.** `/p/<handle>` (`app/portfolio/routes.py`) shows the published page with the
  owner's settings as they are now, so a change to them shows at once. A handle nobody has, and a
  page that isn't published, aren't found. The owner's preview is the same page.
- **Confidential terms** never show (NFR-PRIV-4), as the owner's terms are now, each time the page
  or a resume is asked for (`app/portfolio/confidential.py`). A page that names one, in what it
  shows or in its handle, can't be published, and while a published page names one, it isn't
  found, as though it weren't published: it shows again, at once, once the term is gone (taken
  out of the profile and refreshed, hidden, or no longer a term). A resume whose PDF or button
  text names one can't be chosen, and one chosen before isn't offered or served. The Portfolio
  page names each term and where it is, and the owner's preview still shows the page, so they
  can find it.

The publishing state is a settings key, `portfolio_publishing` (`app/db/settings.py`).
"""

import uuid
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApplicationRow, ProfileVersionRow, Refresh, User
from app.db.profiles import VersionError, get_version
from app.db.settings import (
    get_confidential_terms,
    get_portfolio_publishing,
    get_portfolio_settings,
    portfolio_owner,
    save_portfolio_publishing,
)
from app.db.store import get_artifact
from app.portfolio.build import STATIC_ASSETS, Assets, Link, portfolio_page, render_page
from app.portfolio.confidential import PDF_TERMS, page_matches
from app.schema.common import Id, Model
from app.schema.jobs import Application
from app.schema.profile import Profile
from app.schema.settings import (
    MAX_RESUMES,
    PortfolioPublishing,
    PortfolioSettings,
    PublishedResume,
    ResumeLabel,
)
from app.tailoring.applications import ResumeChecks
from app.validate.confidential import TermMatch, describe, find_terms, quoted

ResumeSource = Literal["master", "application"]
PDF = "application/pdf"


class NotPublishable(Exception):
    """The page can't be published, or can't offer a resume, for the reason given."""


class ResumeOption(Model):
    """A resume the owner may offer on the page."""

    source: ResumeSource
    application_id: Id | None = None
    name: str
    """What it is, for the owner, such as `Master resume (version 4)`."""
    label: ResumeLabel
    """A button text to suggest, such as `Resume`. A tailored resume's leaves out the company."""
    confidential_terms: tuple[str, ...] = ()
    """The confidential terms its PDF names: it can't be offered until they're taken out."""


class ChosenResume(Model):
    """A resume the owner chose to offer."""

    source: ResumeSource
    application_id: Id | None = None
    label: ResumeLabel
    available: bool
    """Whether the page offers it now: false while there's no PDF that may be sent, or while it
    names a confidential term."""
    confidential_terms: tuple[str, ...] = ()
    """The confidential terms its PDF or its button text names, which keep it off the page."""


class PortfolioView(Model):
    """What the Portfolio page shows."""

    handle: str | None
    path: str | None
    """The public page's path, `/p/<handle>`; None until the owner chooses a handle."""
    published: bool
    published_at: datetime | None
    version: int | None
    """The profile version the page shows, or would show if it were published now. None without
    a profile."""
    newest_version: int | None
    """The newest profile version, which may be newer than the page's until the next refresh."""
    settings: PortfolioSettings
    resumes: tuple[ChosenResume, ...]
    options: tuple[ResumeOption, ...]
    """Every resume the owner may offer, the master resume first."""
    confidential: tuple[TermMatch, ...] = ()
    """The confidential terms the page names, and where (NFR-PRIV-4). While it names any, it
    can't be published, and if it is, visitors get "Page not found"."""


@dataclass(frozen=True)
class Offered:
    """A resume the page offers: its button's text and its stored PDF."""

    label: str
    sha256: str


@dataclass(frozen=True)
class Published:
    """A published page's owner, settings and publishing state."""

    user_id: uuid.UUID
    handle: str
    settings: PortfolioSettings
    publishing: PortfolioPublishing
    terms: tuple[str, ...]
    """The owner's confidential terms."""


@dataclass(frozen=True)
class PortfolioContent:
    """Everything the page is rendered from."""

    handle: str | None
    settings: PortfolioSettings
    version: int
    profile: Profile
    resumes: tuple[Offered, ...]
    """The resumes it offers: only those that name no confidential term."""
    terms: tuple[str, ...]
    """The owner's confidential terms, which the page must never name."""


@dataclass(frozen=True)
class _Available:
    key: str
    source: ResumeSource
    application_id: str | None
    sha256: str
    name: str
    label: str


async def refreshed_version(session: AsyncSession, user_id: uuid.UUID) -> int | None:
    """The profile version of the latest refresh whose outputs fell due; None before the first.
    Versions only grow, so it's the largest."""
    return await session.scalar(
        select(func.max(Refresh.output_version)).where(Refresh.user_id == user_id)
    )


async def newest_version(session: AsyncSession, user_id: uuid.UUID) -> int | None:
    return await session.scalar(
        select(func.max(ProfileVersionRow.number)).where(ProfileVersionRow.user_id == user_id)
    )


async def outputs_version(session: AsyncSession, user_id: uuid.UUID) -> int | None:
    """The version the outputs show: the latest refresh's, or the newest before the first
    refresh. None without a profile."""
    return await refreshed_version(session, user_id) or await newest_version(session, user_id)


def shown_version(publishing: PortfolioPublishing, refreshed: int | None) -> int | None:
    """The version a published page shows: the one it was published with, or a later refresh's.
    None when it isn't published."""
    if not publishing.published or publishing.version is None:
        return None
    return max(publishing.version, refreshed or 0)


async def find_published(session: AsyncSession, handle: str) -> Published | None:
    """The page with the handle `handle`, if it's published; None otherwise, whether or not
    someone has the handle, and also while the handle names a confidential term."""
    user_id = await portfolio_owner(session, handle)
    if user_id is None:
        return None
    publishing = await get_portfolio_publishing(session, user_id)
    settings = await get_portfolio_settings(session, user_id)
    if not publishing.published or settings.handle != handle:
        return None
    terms = (await get_confidential_terms(session, user_id)).terms
    if find_terms(handle, terms):
        return None
    return Published(
        user_id=user_id, handle=handle, settings=settings, publishing=publishing, terms=terms
    )


async def published_content(session: AsyncSession, published: Published) -> PortfolioContent | None:
    """What the published page shows."""
    version = shown_version(
        published.publishing, await refreshed_version(session, published.user_id)
    )
    return await _content(
        session, published.user_id, published.settings, published.publishing, version
    )


async def preview_content(session: AsyncSession, user_id: uuid.UUID) -> PortfolioContent | None:
    """The page as it's published, or as publishing it now would show it, with the resumes the
    owner chose. None without a profile."""
    publishing = await get_portfolio_publishing(session, user_id)
    settings = await get_portfolio_settings(session, user_id)
    version = await _page_version(session, user_id, publishing)
    return await _content(session, user_id, settings, publishing, version)


async def offered_resumes(
    session: AsyncSession, user_id: uuid.UUID, chosen: Sequence[PublishedResume]
) -> tuple[Offered, ...]:
    """The PDFs of the chosen resumes that may be offered now, in the owner's order: those that
    passed their checks and name no confidential term, in their PDF or their button text. Only
    these are served."""
    if not chosen:
        return ()
    wanted = {resume.application_id for resume in chosen if resume.application_id is not None}
    available = {
        option.key: option
        for option in await _available(
            session, user_id, applications=wanted, master=any(r.source == "master" for r in chosen)
        )
    }
    terms = (await get_confidential_terms(session, user_id)).terms
    offered: list[Offered] = []
    for resume in chosen:
        option = available.get(resume.key)
        if option is None or find_terms(resume.label, terms):
            continue
        if await pdf_terms(session, user_id, option.sha256, terms) == ():
            offered.append(Offered(label=resume.label, sha256=option.sha256))
    return tuple(offered)


async def pdf_terms(
    session: AsyncSession, user_id: uuid.UUID, sha256: str, terms: Sequence[str]
) -> tuple[str, ...] | None:
    """The confidential terms the stored PDF names; None when it can't be read, so can't be
    checked. Nothing to check without terms."""
    if not terms:
        return ()
    if not PDF_TERMS.known(sha256):
        stored = await get_artifact(session, user_id, sha256)
        if stored is None or stored[0] != PDF:
            return None
        PDF_TERMS.remember(sha256, stored[1])
    return PDF_TERMS.terms(sha256, terms)


def page_html(
    content: PortfolioContent,
    *,
    site_url: str | None = None,
    resume_href: Callable[[str], str] = lambda _: "#",
    assets: Assets = STATIC_ASSETS,
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
        assets=assets,
    )
    return render_page(page)


def content_matches(content: PortfolioContent, html: str | None = None) -> tuple[TermMatch, ...]:
    """The confidential terms the page names, in `html` as served, or as `page_html` renders
    it, and in its handle."""
    if not content.terms:
        return ()
    shown = page_html(content) if html is None else html
    return page_matches(shown, handle=content.handle, terms=content.terms)


async def portfolio_view(session: AsyncSession, user_id: uuid.UUID) -> PortfolioView:
    settings = await get_portfolio_settings(session, user_id)
    publishing = await get_portfolio_publishing(session, user_id)
    terms = (await get_confidential_terms(session, user_id)).terms
    options = await _available(session, user_id)
    found = {
        option.key: await pdf_terms(session, user_id, option.sha256, terms) for option in options
    }
    available = [option for option in options if found[option.key] is not None]
    keys = {option.key for option in available}
    content = await preview_content(session, user_id)

    def named(resume: PublishedResume) -> tuple[str, ...]:
        pdf = found.get(resume.key) or ()
        return tuple(dict.fromkeys((*pdf, *find_terms(resume.label, terms))))

    return PortfolioView(
        handle=settings.handle,
        path=None if settings.handle is None else f"/p/{settings.handle}",
        published=publishing.published,
        published_at=publishing.published_at,
        version=await _page_version(session, user_id, publishing),
        newest_version=await newest_version(session, user_id),
        settings=settings,
        resumes=tuple(
            ChosenResume(
                source=resume.source,
                application_id=resume.application_id,
                label=resume.label,
                available=resume.key in keys and not named(resume),
                confidential_terms=named(resume),
            )
            for resume in publishing.resumes
        ),
        options=tuple(
            ResumeOption(
                source=option.source,
                application_id=option.application_id,
                name=option.name,
                label=option.label,
                confidential_terms=found[option.key] or (),
            )
            for option in available
        ),
        confidential=() if content is None else content_matches(content),
    )


async def publish(session: AsyncSession, user_id: uuid.UUID, *, now: datetime) -> None:
    """Publish the page, which shows at once (FR-PRT-3), from the version the outputs show now.
    Publishing a published page changes nothing. Raises `NotPublishable` without a handle or a
    profile, or when the page names a confidential term (NFR-PRIV-4)."""
    await _lock(session, user_id)
    current = await get_portfolio_publishing(session, user_id)
    if current.published:
        return
    settings = await get_portfolio_settings(session, user_id)
    if settings.handle is None:
        raise NotPublishable("Choose the page's handle in Settings first")
    version = await outputs_version(session, user_id)
    if version is None:
        raise NotPublishable("There's no profile to publish yet")
    content = await _content(session, user_id, settings, current, version)
    matches = () if content is None else content_matches(content)
    if matches:
        raise NotPublishable(
            f"The page names your confidential terms, so it can't be published: {describe(matches)}"
        )
    await save_portfolio_publishing(
        session,
        user_id,
        PortfolioPublishing(
            published=True, version=version, published_at=now, resumes=current.resumes
        ),
    )


async def unpublish(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Take the page down. The resumes chosen are kept for the next time."""
    await _lock(session, user_id)
    current = await get_portfolio_publishing(session, user_id)
    await save_portfolio_publishing(session, user_id, PortfolioPublishing(resumes=current.resumes))


async def choose_resumes(
    session: AsyncSession, user_id: uuid.UUID, resumes: Sequence[PublishedResume]
) -> None:
    """Replace the resumes the page offers. Raises `NotPublishable` for one that can't be
    offered, such as a tailored resume with an error."""
    await _lock(session, user_id)
    current = await get_portfolio_publishing(session, user_id)
    available = {option.key: option for option in await _available(session, user_id)}
    known = {resume.key for resume in current.resumes}
    terms = (await get_confidential_terms(session, user_id)).terms
    for resume in resumes:
        what = "the master resume" if resume.source == "master" else "that tailored resume"
        if named := find_terms(resume.label, terms):
            raise NotPublishable(
                f"The button text for {what} names your confidential term {quoted(named)}"
            )
        # A resume chosen before stays chosen, even while it isn't available.
        if resume.key in known:
            continue
        option = available.get(resume.key)
        if option is None:
            raise NotPublishable(f"The page can't offer {what}: it has no PDF that may be sent")
        found = await pdf_terms(session, user_id, option.sha256, terms)
        if found is None:
            raise NotPublishable(f"The page can't offer {what}: its PDF can't be read to check it")
        if found:
            raise NotPublishable(
                f"The page can't offer {what}: it names your confidential term {quoted(found)}"
            )
    try:
        changed = PortfolioPublishing.model_validate(
            current.model_dump() | {"resumes": [resume.model_dump() for resume in resumes]}
        )
    except ValidationError:
        raise NotPublishable(f"Choose up to {MAX_RESUMES} resumes, each once") from None
    await save_portfolio_publishing(session, user_id, changed)


async def _page_version(
    session: AsyncSession, user_id: uuid.UUID, publishing: PortfolioPublishing
) -> int | None:
    if publishing.published:
        return shown_version(publishing, await refreshed_version(session, user_id))
    return await outputs_version(session, user_id)


async def _content(
    session: AsyncSession,
    user_id: uuid.UUID,
    settings: PortfolioSettings,
    publishing: PortfolioPublishing,
    version: int | None,
) -> PortfolioContent | None:
    if version is None:
        return None
    try:
        profile = (await get_version(session, user_id, version)).profile
    except VersionError:
        return None
    return PortfolioContent(
        handle=settings.handle,
        settings=settings,
        version=version,
        profile=profile,
        resumes=await offered_resumes(session, user_id, publishing.resumes),
        terms=(await get_confidential_terms(session, user_id)).terms,
    )


async def _available(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    applications: Collection[str] | None = None,
    master: bool = True,
) -> list[_Available]:
    """The resumes that may be offered: the master resume the latest refresh rendered, then
    the tailored resumes with a PDF that passed their checks, newest first. `applications`
    narrows the tailored ones to those IDs."""
    found: list[_Available] = []
    if master:
        latest = (
            await session.execute(
                select(Refresh.master_pdf_sha256, Refresh.output_version)
                .where(Refresh.user_id == user_id, Refresh.master_pdf_sha256.is_not(None))
                .order_by(Refresh.output_version.desc(), Refresh.outputs_at.desc())
                .limit(1)
            )
        ).one_or_none()
        sha256, version = latest if latest is not None else (None, None)
        if sha256 is not None:
            found.append(
                _Available(
                    key="master",
                    source="master",
                    application_id=None,
                    sha256=sha256,
                    name=f"Master resume (version {version})",
                    label="Resume",
                )
            )
    if applications is not None and not applications:
        return found
    query = select(ApplicationRow).where(
        ApplicationRow.user_id == user_id, ApplicationRow.pdf_sha256.is_not(None)
    )
    if applications is not None:
        query = query.where(ApplicationRow.id.in_(applications))
    rows = await session.scalars(
        query.order_by(ApplicationRow.applied_on.desc(), ApplicationRow.created_at.desc())
    )
    for row in rows:
        if row.pdf_sha256 is None or not _sendable(row.verifier_report):
            continue
        try:
            application = Application.model_validate(row.data)
        except ValidationError:
            continue
        found.append(
            _Available(
                key=f"application:{row.id}",
                source="application",
                application_id=row.id,
                sha256=row.pdf_sha256,
                name=_application_name(application),
                label=f"{application.position} resume"[:60].strip(),
            )
        )
    return found


def _sendable(report: dict[str, object] | None) -> bool:
    """Whether a tailored resume passed its checks: no resume rule's error, and every text the
    verifier passed. One without its checks isn't sendable."""
    if report is None:
        return False
    try:
        checks = ResumeChecks.model_validate(report)
    except ValidationError:
        return False
    return checks.rules.ok and all(text.passed for text in checks.texts)


def _application_name(application: Application) -> str:
    applied: date = application.applied_on
    return f"Tailored: {application.position} at {application.company} ({applied.isoformat()})"


async def _lock(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Hold the user's row until the transaction ends, so two changes to the publishing state
    can't undo each other."""
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())
