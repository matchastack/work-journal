"""Refreshes: profile changes only when the owner asks, then the outputs from the new version
(FR-REV-6, FR-REV-7, FR-RES-5).

1. `/refresh` (`app/telegram/refresh.py`) or `wj propose --db` starts a refresh
   (`start_refresh`), unless one is running, proposed changes still wait for review, or there's
   no profile yet.
2. Its job (`refresh_profile` in `app/jobs.py`) proposes changes from the journal facts that no
   refresh has covered: new facts, and facts edited since (`facts.refreshed_at`). It reads them
   (`begin_proposals`), runs profile synthesis (T-018) outside a transaction, then saves the
   change set for review and marks the facts covered (`record_proposals`). A fact edited in the
   meantime isn't marked, so the next refresh covers it again. A fact the change set doesn't use
   is covered too: it waits in the brag bank until it's edited.
3. A refresh's outputs fall due (`outputs_due`) when its change set is decided, in the Inbox or
   with `wj apply --db` (`change_set_decided`), or at once when it proposes nothing. They're to
   show the newest version then, so edits the owner made since the last refresh reach them too.
4. The `update_outputs` job renders the master resume from that version and keeps the PDF
   (`outputs_to_update`, `record_outputs`). Rendering needs LaTeX, which Vercel lacks (C6), so the
   job waits for a worker that has it (`LATEX_QUEUE` in `app/jobs.py`). A render of the same
   LaTeX source kept before, such as a refresh's with nothing new, is used again rather than
   compiled (`render_key`). Tailored resumes made before are never changed.
5. The `write_linkedin_pack` job writes the LinkedIn pack from the same version, as
   `wj linkedin --db` does, and saves it as the user's pack (`pack_to_write`, `record_pack`). It
   needs no LaTeX, so it runs wherever jobs run, apart from the master resume. Each section is
   kept as it's written (`keep_pack_progress`), so a try stopped at the tick's time limit leaves
   them for the next.

Nothing here runs on a schedule or when an entry closes. Fact text is never logged.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import selection
from app.db.models import ChangeSetRow, FactRow, ProfileVersionRow, Refresh, User
from app.db.profiles import Version, get_version, latest_version, propose_change_set
from app.db.settings import (
    PackInputs,
    clear_linkedin_draft,
    get_confidential_terms,
    get_linkedin_draft,
    get_style_notes,
    linkedin_pack_inputs,
    save_linkedin_draft,
    save_linkedin_pack,
)
from app.db.store import save_render
from app.linkedin import LinkedInPack, PackSection
from app.render.resume import RenderedResume
from app.schema.fact import Fact
from app.schema.profile import Profile
from app.selection import Selection
from app.synthesis import Synthesis

UNFINISHED_AFTER = timedelta(minutes=30)
"""How long after a try at a refresh started it counts as unfinished: well past the 300 s a try
may run in a tick, and time enough for `wj propose --db`, which has no limit. Until then, another
try leaves it to the one that started it."""


@dataclass(frozen=True)
class Blocked:
    """Why a refresh can't start now."""

    reason: Literal["no_profile", "running", "waiting"]
    """There's no profile yet, a refresh is running, or proposed changes wait for review."""
    running: uuid.UUID | None = None
    """The refresh that's running, if one is."""


class RefreshInterrupted(Exception):
    """An earlier try at the refresh started more than `UNFINISHED_AFTER` ago and never finished,
    such as one whose function was stopped. It isn't tried again: each try calls the heavy-tier
    model."""

    def __init__(self, user_id: uuid.UUID) -> None:
        super().__init__("an earlier try at the refresh didn't finish")
        self.user_id = user_id


@dataclass(frozen=True)
class RefreshInputs:
    """What a refresh proposes changes from."""

    refresh_id: uuid.UUID
    user_id: uuid.UUID
    version: Version
    """The newest profile version, which the changes are proposed against."""
    facts: tuple[Fact, ...]
    """The journal facts that no refresh has covered."""
    read: Mapping[str, datetime]
    """When each of `facts` was last saved, as read: one saved since is left for the next
    refresh."""
    sources: tuple[Fact, ...]
    """Every fact, for the bullets that new versions are proposed for."""
    avoid: tuple[str, ...]
    """The owner's words to avoid (FR-WRT-4)."""
    confidential: tuple[str, ...] = ()
    """The owner's confidential terms (FR-SET-5), which the verifier checks each bullet for."""


@dataclass(frozen=True)
class Proposals:
    """What a refresh proposed."""

    user_id: uuid.UUID
    facts: int
    """How many facts it covered."""
    changes: int
    """How many changes wait for review: none when it proposed nothing."""


@dataclass(frozen=True)
class OutputsJob:
    """A refresh's outputs, to update from one profile version."""

    refresh_id: uuid.UUID
    user_id: uuid.UUID
    version: int
    profile: Profile


@dataclass(frozen=True)
class PackJob:
    """A refresh's LinkedIn pack, to write from one profile version."""

    refresh_id: uuid.UUID
    user_id: uuid.UUID
    version: int
    profile: Profile
    inputs: PackInputs
    written: tuple[PackSection, ...]
    """The sections an earlier try wrote from this version, which aren't written again."""


@dataclass(frozen=True)
class PackUpdate:
    """Where the LinkedIn pack of the newest refresh with one stands."""

    status: Literal["pending", "updated", "failed"]
    version: int
    """The profile version it's written from."""


async def start_refresh(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | Blocked:
    """Start a refresh for the user, and return its ID, or why it can't start."""
    # Lock the user, so that two requests can't both start one.
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())
    running = await session.scalar(
        select(Refresh.id).where(Refresh.user_id == user_id, Refresh.status == "running")
    )
    if running is not None:
        return Blocked("running", running)
    profile = await session.scalar(
        select(ProfileVersionRow.number).where(ProfileVersionRow.user_id == user_id).limit(1)
    )
    if profile is None:
        return Blocked("no_profile")
    waiting = await session.scalar(
        select(ChangeSetRow.id)
        .where(ChangeSetRow.user_id == user_id, ChangeSetRow.status == "proposed")
        .limit(1)
    )
    if waiting is not None:
        return Blocked("waiting")
    refresh = Refresh(user_id=user_id)
    session.add(refresh)
    await session.flush()
    return refresh.id


async def running_refreshes(session: AsyncSession) -> list[uuid.UUID]:
    """Every running refresh, oldest first."""
    rows = await session.scalars(
        select(Refresh.id).where(Refresh.status == "running").order_by(Refresh.requested_at)
    )
    return list(rows)


async def begin_proposals(
    session: AsyncSession, refresh_id: uuid.UUID, *, now: datetime
) -> RefreshInputs | None:
    """Mark the refresh started, and read what it proposes changes from. Returns None when it
    isn't running, such as when it's done already, or when another try started it recently and
    may still be at work, such as `wj propose --db`.

    Raises `RefreshInterrupted` when an earlier try started it long ago and didn't finish.
    """
    refresh = await session.scalar(
        select(Refresh).where(Refresh.id == refresh_id).with_for_update()
    )
    if refresh is None or refresh.status != "running":
        return None
    if refresh.started_at is not None:
        if refresh.started_at >= now - UNFINISHED_AFTER:
            return None
        raise RefreshInterrupted(refresh.user_id)
    version = await latest_version(session, refresh.user_id)
    if version is None:
        return None
    refresh.started_at = now
    rows = await session.execute(
        select(FactRow.data, FactRow.origin, FactRow.updated_at, FactRow.refreshed_at)
        .where(FactRow.user_id == refresh.user_id)
        .order_by(FactRow.id)
    )
    sources: list[Fact] = []
    new: list[Fact] = []
    read: dict[str, datetime] = {}
    for data, origin, updated_at, refreshed_at in rows:
        fact = Fact.model_validate_json(data)
        sources.append(fact)
        if origin == "journal" and refreshed_at is None:
            new.append(fact)
            read[fact.id] = updated_at
    avoid = (await get_style_notes(session, refresh.user_id)).avoid
    confidential = (await get_confidential_terms(session, refresh.user_id)).terms
    return RefreshInputs(
        refresh_id=refresh.id,
        user_id=refresh.user_id,
        version=version,
        facts=tuple(new),
        read=read,
        sources=tuple(sources),
        avoid=avoid,
        confidential=confidential,
    )


async def record_proposals(
    session: AsyncSession, inputs: RefreshInputs, synthesis: Synthesis, *, now: datetime
) -> Proposals | None:
    """Save the proposed changes for review, mark the facts the refresh covered, and finish it.
    When it proposes nothing, its outputs fall due at once. Returns None, saving nothing, when
    the refresh isn't running any more."""
    refresh = await session.scalar(
        select(Refresh).where(Refresh.id == inputs.refresh_id).with_for_update()
    )
    if refresh is None or refresh.status != "running":
        return None
    change_set = synthesis.change_set
    if change_set is not None:
        refresh.change_set_id = await propose_change_set(session, inputs.user_id, change_set)
    if inputs.read:
        # A fact saved since it was read has a newer time, and stays for the next refresh.
        await session.execute(
            update(FactRow)
            .where(
                FactRow.user_id == inputs.user_id,
                tuple_(FactRow.id, FactRow.updated_at).in_(list(inputs.read.items())),
            )
            .values(refreshed_at=now)
        )
    refresh.status = "nothing_new" if change_set is None else "proposed"
    refresh.finished_at = now
    refresh.fact_count = len(inputs.facts)
    if change_set is None:
        await outputs_due(session, refresh, now=now)
    await session.flush()
    changes = 0 if change_set is None else len(change_set.ops)
    return Proposals(user_id=inputs.user_id, facts=len(inputs.facts), changes=changes)


async def fail_refresh(
    session: AsyncSession, refresh_id: uuid.UUID, *, now: datetime
) -> uuid.UUID | None:
    """Mark a running refresh failed: its facts wait for the next one. Returns its user, or None
    when it wasn't running."""
    refresh = await session.scalar(
        select(Refresh).where(Refresh.id == refresh_id).with_for_update()
    )
    if refresh is None or refresh.status != "running":
        return None
    refresh.status = "failed"
    refresh.finished_at = now
    return refresh.user_id


async def change_set_decided(
    session: AsyncSession, user_id: uuid.UUID, change_set_id: uuid.UUID, *, now: datetime
) -> None:
    """The owner has decided on every change in a change set, and applied the accepted ones. If
    a refresh proposed it, the refresh's outputs fall due (FR-REV-7)."""
    refresh = await session.scalar(
        select(Refresh)
        .where(Refresh.user_id == user_id, Refresh.change_set_id == change_set_id)
        .with_for_update()
    )
    if refresh is not None:
        await outputs_due(session, refresh, now=now)


async def outputs_due(session: AsyncSession, refresh: Refresh, *, now: datetime) -> None:
    """The refresh's outputs, the master resume and the LinkedIn pack, are to show the newest
    profile version. An earlier refresh's that still wait are superseded."""
    newest = await session.scalar(
        select(func.max(ProfileVersionRow.number)).where(
            ProfileVersionRow.user_id == refresh.user_id
        )
    )
    if newest is None:
        return
    await session.execute(
        update(Refresh)
        .where(
            Refresh.user_id == refresh.user_id,
            Refresh.outputs == "pending",
            Refresh.id != refresh.id,
        )
        .values(outputs="superseded", outputs_at=now)
    )
    await session.execute(
        update(Refresh)
        .where(
            Refresh.user_id == refresh.user_id,
            Refresh.linkedin_pack == "pending",
            Refresh.id != refresh.id,
        )
        .values(linkedin_pack="superseded", linkedin_pack_at=now)
    )
    refresh.outputs = "pending"
    refresh.output_version = newest
    refresh.outputs_at = now
    refresh.master_pdf_sha256 = None
    refresh.master_pages = None
    refresh.linkedin_pack = "pending"
    refresh.linkedin_pack_at = now


async def due_outputs(session: AsyncSession) -> list[uuid.UUID]:
    """The refreshes whose outputs wait to be updated, at most one per user, oldest first."""
    rows = await session.scalars(
        select(Refresh.id).where(Refresh.outputs == "pending").order_by(Refresh.outputs_at)
    )
    return list(rows)


async def outputs_to_update(session: AsyncSession, refresh_id: uuid.UUID) -> OutputsJob | None:
    """The refresh's outputs and the version to update them from, or None when they don't wait
    to be updated."""
    refresh = await session.get(Refresh, refresh_id)
    if refresh is None or refresh.outputs != "pending" or refresh.output_version is None:
        return None
    version = await get_version(session, refresh.user_id, refresh.output_version)
    return OutputsJob(
        refresh_id=refresh.id,
        user_id=refresh.user_id,
        version=version.number,
        profile=version.profile,
    )


def master_selection(profile: Profile) -> Selection:
    """What the full master document shows (FR-RES-5, FR-RES-6)."""
    return selection.select(profile, selection.MASTER_VARIANT)


async def record_outputs(
    session: AsyncSession, job: OutputsJob, rendered: RenderedResume | None, *, now: datetime
) -> bool:
    """Keep the master resume rendered for the refresh, with its render (`save_render`), or mark
    its outputs failed when it couldn't be rendered (None). Returns False, changing nothing, when
    they don't wait for this update any more, such as when a later refresh's fell due."""
    refresh = await session.scalar(
        select(Refresh).where(Refresh.id == job.refresh_id).with_for_update()
    )
    if refresh is None or refresh.outputs != "pending" or refresh.output_version != job.version:
        return False
    refresh.outputs_at = now
    if rendered is None:
        refresh.outputs = "failed"
        return True
    refresh.master_pdf_sha256 = await save_render(session, job.user_id, rendered)
    refresh.master_pages = rendered.pages
    refresh.outputs = "updated"
    return True


async def due_packs(session: AsyncSession) -> list[uuid.UUID]:
    """The refreshes whose LinkedIn pack waits to be written, at most one per user, oldest
    first."""
    rows = await session.scalars(
        select(Refresh.id)
        .where(Refresh.linkedin_pack == "pending")
        .order_by(Refresh.linkedin_pack_at)
    )
    return list(rows)


async def pack_to_write(session: AsyncSession, refresh_id: uuid.UUID) -> PackJob | None:
    """The refresh's LinkedIn pack and what to write it from, with the sections an earlier try
    wrote from the same version; None when it doesn't wait to be written."""
    refresh = await session.get(Refresh, refresh_id)
    if refresh is None or refresh.linkedin_pack != "pending" or refresh.output_version is None:
        return None
    version = await get_version(session, refresh.user_id, refresh.output_version)
    draft = await get_linkedin_draft(session, refresh.user_id)
    written = draft.sections if draft and draft.profile_version == version.number else ()
    return PackJob(
        refresh_id=refresh.id,
        user_id=refresh.user_id,
        version=version.number,
        profile=version.profile,
        inputs=await linkedin_pack_inputs(session, refresh.user_id),
        written=written,
    )


async def keep_pack_progress(
    session: AsyncSession, job: PackJob, written: Sequence[PackSection]
) -> None:
    """Keep the sections written so far from the job's version, for a try that follows a stop."""
    draft = LinkedInPack(sections=tuple(written), profile_version=job.version)
    await save_linkedin_draft(session, job.user_id, draft)


async def record_pack(
    session: AsyncSession, job: PackJob, pack: LinkedInPack | None, *, now: datetime
) -> bool:
    """Save the pack written for the refresh as the user's pack, or mark it failed when it
    couldn't be written (None). Returns False, changing nothing, when it doesn't wait for this
    update any more, such as when a later refresh's outputs fell due."""
    refresh = await session.scalar(
        select(Refresh).where(Refresh.id == job.refresh_id).with_for_update()
    )
    if (
        refresh is None
        or refresh.linkedin_pack != "pending"
        or refresh.output_version != job.version
    ):
        return False
    refresh.linkedin_pack_at = now
    if pack is None:
        refresh.linkedin_pack = "failed"
        return True
    await save_linkedin_pack(session, job.user_id, pack)
    await clear_linkedin_draft(session, job.user_id)
    refresh.linkedin_pack = "updated"
    return True


async def latest_pack_update(session: AsyncSession, user_id: uuid.UUID) -> PackUpdate | None:
    """Where the LinkedIn pack of the user's newest refresh with one stands; None before the
    first. Refreshes run one after another, so only an earlier one's can be superseded."""
    row = (
        await session.execute(
            select(Refresh.linkedin_pack, Refresh.output_version)
            .where(Refresh.user_id == user_id, Refresh.linkedin_pack.is_not(None))
            .order_by(Refresh.requested_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    status, version = row
    if status is None or status == "superseded" or version is None:
        return None
    return PackUpdate(status=status, version=version)
