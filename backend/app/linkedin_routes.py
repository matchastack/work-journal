"""`/api/linkedin`: the LinkedIn page (T-047). It lists the sections of the LinkedIn pack that
changed since the owner last marked it done, to paste into LinkedIn, and marks it done.

The page shows the pack last written (`app/linkedin.py`), from the profile version it names:
`wj linkedin --db` writes it, and the refresh will too (T-061). The page never writes the pack,
so it makes no call to Claude, and every section on it passed the verifier when it was written,
or is flagged. Each section is screened for the owner's confidential terms as they are now, when
the page loads and when it's marked done (NFR-PRIV-4): one that names a term is flagged and
listed, even if it was marked done before, and isn't marked done until it's rewritten.
"""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.routes import NO_STORE
from app.auth.sessions import CurrentUser
from app.db.profiles import latest_version
from app.db.settings import (
    get_confidential_terms,
    get_linkedin_done,
    get_linkedin_pack,
    save_linkedin_done,
)
from app.deps import Db
from app.linkedin import LinkedInPack, PackSection, mark_done, screen_pack, since_done
from app.schema.common import Model, NonEmptyStr
from app.schema.verification import VerifierReport
from app.validate.style import LinkedInSection

router = APIRouter(prefix="/api/linkedin", tags=["linkedin"])


class LinkedInPageSection(Model):
    """A section to paste into LinkedIn."""

    key: NonEmptyStr
    """`headline`, `about` or `position:<role ID>`."""
    kind: LinkedInSection
    label: NonEmptyStr
    """E.g. `Position: Backend Engineer at Northwind Traders`, with the title to use."""
    text: NonEmptyStr
    length: int
    """The length as LinkedIn counts it."""
    limit: int
    """LinkedIn's limit for the section (FR-LIN-1)."""
    flagged: bool
    """True when the text failed the verifier or names a confidential term: it's not marked done
    with the others."""
    problems: tuple[str, ...]
    """Each confidential term the text or title names, in words: it mustn't be pasted as it is."""
    report: VerifierReport | None
    """What the verifier found when the section was written."""
    notes: tuple[str, ...]
    """Things to check, such as a title that differs from the latest application's."""


class LinkedInPagePack(Model):
    digest: NonEmptyStr
    """Identifies the pack: a SHA-256 of it, in hex. Marking it done sends it back, so a pack
    written since the page loaded is never marked done unseen."""
    profile_version: int | None
    """The profile version the pack was written from."""
    sections: tuple[LinkedInPageSection, ...]
    """The sections that changed since the pack was last marked done, or name a confidential
    term, in the pack's order."""
    unchanged: int
    """How many sections are as they were last marked done, and name no confidential term."""
    removed: tuple[str, ...]
    """Sections marked done before that the profile no longer has: take them off LinkedIn."""


class LinkedInPage(Model):
    pack: LinkedInPagePack | None
    """None until a pack is written."""
    profile_version: int | None
    """The newest profile version; None before the profile is loaded."""
    marked_at: datetime | None
    """When a pack was last marked done; None until one is."""


class MarkDone(Model):
    digest: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    """The digest of the pack the owner pasted."""


class Marked(Model):
    marked: int
    """How many sections the snapshot now holds from this pack."""
    flagged: tuple[str, ...]
    """The labels of the sections left out, as the verifier flagged them or they name a
    confidential term."""


@router.get("")
async def linkedin_page(user: CurrentUser, db: Db, response: Response) -> LinkedInPage:
    """The LinkedIn pack's sections that changed since it was last marked done (FR-LIN-2), each
    with its length and LinkedIn's limit (FR-LIN-3)."""
    response.headers.update(NO_STORE)
    pack = await get_linkedin_pack(db, user.id)
    done = await get_linkedin_done(db, user.id)
    version = await latest_version(db, user.id)
    view = None
    if pack is not None:
        current = since_done(await _screened(db, user.id, pack), done)
        listed = [s for s in current.sections if s.changed or s.confidential_terms]
        view = LinkedInPagePack(
            digest=_digest(pack),
            profile_version=pack.profile_version,
            sections=tuple(_section(section) for section in listed),
            unchanged=len(current.sections) - len(listed),
            removed=current.removed,
        )
    return LinkedInPage(
        pack=view,
        profile_version=version.number if version else None,
        marked_at=done.marked_at if done else None,
    )


@router.post("/done")
async def mark_pack_done(body: MarkDone, user: CurrentUser, db: Db, response: Response) -> Marked:
    """Mark the pack done once its sections are pasted into LinkedIn: they're stored as the
    snapshot that later packs are compared with (FR-LIN-3). A flagged section, including one
    that names a confidential term now, is left out, so it's written again. 409 when there's no
    pack, or it isn't the one with `digest`."""
    response.headers.update(NO_STORE)
    pack = await get_linkedin_pack(db, user.id)
    if pack is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "there's no LinkedIn pack to mark done")
    if _digest(pack) != body.digest:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "the pack was written again since the page loaded; check its sections first",
        )
    previous = await get_linkedin_done(db, user.id)
    screened = await _screened(db, user.id, pack)
    done, flagged = mark_done(screened, previous, now=datetime.now(UTC))
    await save_linkedin_done(db, user.id, done)
    await db.commit()
    return Marked(
        marked=len(pack.sections) - len(flagged),
        flagged=tuple(section.label for section in flagged),
    )


def _section(section: PackSection) -> LinkedInPageSection:
    return LinkedInPageSection(
        key=section.key,
        kind=section.kind,
        label=section.label,
        text=section.text,
        length=section.length,
        limit=section.limit,
        flagged=section.flagged,
        problems=section.problems,
        report=section.report,
        notes=section.notes,
    )


async def _screened(db: AsyncSession, user_id: uuid.UUID, pack: LinkedInPack) -> LinkedInPack:
    """`pack` with each section's confidential terms found as the owner's terms are now."""
    return screen_pack(pack, (await get_confidential_terms(db, user_id)).terms)


def _digest(pack: LinkedInPack) -> str:
    """Of the pack as written: a change to the confidential terms isn't a new pack."""
    return hashlib.sha256(pack.model_dump_json().encode("utf-8")).hexdigest()
