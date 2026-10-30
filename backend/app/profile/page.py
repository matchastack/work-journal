"""What the Profile page reads and saves (T-042).

The page shows the latest version with its resume-rule findings (`lint_profile`), which never
block a save: the master profile may hold work in progress, and sendable resumes are checked on
their own (R2). The version history lists every version with what its change set says it did, and
each version's changes against its parent.

The owner's edits are saved as a change set and a new version (FR-PRF-11), and a restore saves
a copy of an old version as the newest (FR-PRF-3). Either names the version the page showed: the
newest version is locked while it's checked, so a save from a page that's out of date is refused
(`Conflict`) rather than undoing a change made since, such as an Inbox apply in another tab.
Owner edits reach the resumes, the portfolio page and the LinkedIn pack at the next refresh
(FR-REV-7).
"""

import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime

from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.profiles import (
    StaleVersion,
    Version,
    VersionError,
    VersionInfo,
    change_set_summaries,
    get_version,
    latest_version,
    list_versions,
    lock_latest,
    restore_version,
    save_owner_changes,
)
from app.profile.edits import Edited, MissingTarget
from app.profile.history import VersionChange, describe_path, version_changes
from app.schema.changes import Author
from app.schema.common import Model
from app.schema.profile import Profile
from app.validate.lint import Finding, lint_profile


class NotFound(LookupError):
    """The owner's profile has no such version, item or bullet."""


class Conflict(ValueError):
    """The save doesn't fit the profile as it now is, e.g. the profile changed since the page
    loaded."""


class VersionSummary(Model):
    number: int
    parent: int | None
    """The version it was made from."""
    author: Author
    created_at: datetime
    restored_from: int | None
    """The version it restores, if it's a restore."""
    summary: str | None
    """What its change set did; None for a restore and for the profile as first loaded."""


class LintFinding(Finding):
    where: str
    """Where the problem is, in words, e.g. `A bullet under Engineer, Northwind Traders`."""


class LintView(Model):
    findings: tuple[LintFinding, ...]
    """Errors first, then warnings."""
    notes: tuple[str, ...]
    """What the rules couldn't check, e.g. an education rule they can't read."""


class ProfilePage(Model):
    """The latest version, or nulls before a profile is loaded."""

    version: VersionSummary | None
    profile: Profile | None
    lint: LintView | None


class VersionList(Model):
    versions: tuple[VersionSummary, ...]
    """Newest first."""


class VersionDetail(Model):
    version: VersionSummary
    changes: tuple[VersionChange, ...]
    """What changed against its parent; none for the first version."""


class Restore(Model):
    base_version: int = Field(ge=1)
    """The version the page showed. The restore is refused when the profile has moved on since."""


async def load_page(session: AsyncSession, user_id: uuid.UUID) -> ProfilePage:
    version = await latest_version(session, user_id)
    if version is None:
        return ProfilePage(version=None, profile=None, lint=None)
    summaries = await _summaries(session, user_id, [version])
    return ProfilePage(
        version=_summary(version, summaries), profile=version.profile, lint=_lint(version.profile)
    )


async def load_versions(session: AsyncSession, user_id: uuid.UUID) -> VersionList:
    versions = await list_versions(session, user_id)
    summaries = await _summaries(session, user_id, versions)
    return VersionList(versions=tuple(_summary(info, summaries) for info in versions))


async def load_version(session: AsyncSession, user_id: uuid.UUID, number: int) -> VersionDetail:
    """A version, with what it changed against its parent."""
    version = await _version(session, user_id, number)
    parent = None if version.parent is None else await _version(session, user_id, version.parent)
    summaries = await _summaries(session, user_id, [version])
    return VersionDetail(
        version=_summary(version, summaries),
        changes=version_changes(parent.profile if parent else None, version.profile),
    )


async def save_edit(
    session: AsyncSession,
    user_id: uuid.UUID,
    base_version: int,
    build: Callable[[Profile], Edited],
) -> ProfilePage:
    """Save the operations `build` makes from version `base_version`, which must be the newest,
    as a change set by the owner and a new version. An edit that changes nothing saves nothing.
    Raises `ProfileOpError` when the operations don't apply."""
    base = await _lock(session, user_id, base_version)
    try:
        edited = build(base.profile)
    except MissingTarget as error:
        raise NotFound(str(error)) from None
    if edited.ops:
        await save_owner_changes(session, user_id, base, edited.ops, summary=edited.summary)
    return await load_page(session, user_id)


async def restore(
    session: AsyncSession, user_id: uuid.UUID, number: int, base_version: int
) -> ProfilePage:
    """Save a copy of version `number` as the newest version (FR-PRF-3), made from version
    `base_version`, which must be the newest. The versions in between stay."""
    base = await _lock(session, user_id, base_version)
    if number == base.number:
        raise Conflict(f"version {number} is already your current profile")
    old = await _version(session, user_id, number)
    if old.profile == base.profile:
        raise Conflict(f"version {number} is the same as your current profile")
    await restore_version(session, user_id, number)
    return await load_page(session, user_id)


async def _lock(session: AsyncSession, user_id: uuid.UUID, base_version: int) -> Version:
    try:
        return await lock_latest(session, user_id, expected=base_version)
    except StaleVersion as error:
        raise Conflict(
            f"your profile changed since this page loaded: it's now at version {error.latest}, "
            "so check it and try again"
        ) from None
    except VersionError:
        raise Conflict("there's no profile to edit yet: load it with wj db load-profile") from None


async def _version(session: AsyncSession, user_id: uuid.UUID, number: int) -> Version:
    try:
        return await get_version(session, user_id, number)
    except VersionError:
        raise NotFound(f"there's no version {number} of your profile") from None


async def _summaries(
    session: AsyncSession, user_id: uuid.UUID, versions: Iterable[VersionInfo]
) -> dict[uuid.UUID, str | None]:
    ids = [version.change_set_id for version in versions if version.change_set_id is not None]
    return await change_set_summaries(session, user_id, ids)


def _summary(version: VersionInfo, summaries: Mapping[uuid.UUID, str | None]) -> VersionSummary:
    change_set = version.change_set_id
    return VersionSummary(
        number=version.number,
        parent=version.parent,
        author=version.author,
        created_at=version.created_at,
        restored_from=version.restored_from,
        summary=None if change_set is None else summaries.get(change_set),
    )


def _lint(profile: Profile) -> LintView:
    report = lint_profile(profile)
    return LintView(
        findings=tuple(
            LintFinding.model_validate(
                finding.model_dump() | {"where": describe_path(finding.location, profile)}
            )
            for finding in report.findings
        ),
        notes=report.notes,
    )
