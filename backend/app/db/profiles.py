"""Profile versions and change sets.

Every change to the master profile saves a new version, recording its parent, its author and the
change set that made it (FR-PRF-2). Versions are immutable (NFR-DATA-1), so restoring an old one
saves a copy of it as the newest version (FR-PRF-3).
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import JsonValue
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChangeOpRow, ChangeSetRow, ProfileVersionRow, User
from app.schema.changes import (
    Author,
    ChangeOp,
    ChangeSet,
    ChangeSetStatus,
    OpStatus,
    ReviewedOp,
    change_op_adapter,
)
from app.schema.profile import Profile


class VersionError(LookupError):
    """A version or change set doesn't exist, or the request doesn't fit the history."""


@dataclass(frozen=True)
class VersionInfo:
    number: int
    parent: int | None
    """The version this one was made from."""
    author: Author
    change_set_id: uuid.UUID | None
    restored_from: int | None
    """The version this one restores, if it's a restore."""
    created_at: datetime


@dataclass(frozen=True)
class Version(VersionInfo):
    profile: Profile


@dataclass(frozen=True)
class SavedChangeSet:
    id: uuid.UUID
    created_at: datetime
    decided_at: datetime | None
    change_set: ChangeSet


async def save_version(
    session: AsyncSession,
    user_id: uuid.UUID,
    profile: Profile,
    *,
    author: Author,
    change_set_id: uuid.UUID | None = None,
    restored_from: int | None = None,
) -> Version:
    """Save `profile` as the user's next version, made from their latest one."""
    # Lock the user, so two saves can't both take the next number.
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())
    latest = await session.scalar(
        select(func.max(ProfileVersionRow.number)).where(ProfileVersionRow.user_id == user_id)
    )
    row = ProfileVersionRow(
        user_id=user_id,
        number=(latest or 0) + 1,
        parent=latest,
        author=author,
        change_set_id=change_set_id,
        restored_from=restored_from,
        profile=profile.model_dump(mode="json", by_alias=True),
    )
    session.add(row)
    await session.flush()
    await session.refresh(row, ["created_at"])
    return _version(row)


async def load_master_profile(
    session: AsyncSession, user_id: uuid.UUID, profile: Profile
) -> Version:
    """Save the imported master profile as the user's first version."""
    if (latest := await latest_version(session, user_id)) is not None:
        raise VersionError(
            f"the profile is already loaded: the newest version is {latest.number}; "
            "changes now go through change sets"
        )
    return await save_version(session, user_id, profile, author="owner")


async def latest_version(session: AsyncSession, user_id: uuid.UUID) -> Version | None:
    row = await session.scalar(
        select(ProfileVersionRow)
        .where(ProfileVersionRow.user_id == user_id)
        .order_by(ProfileVersionRow.number.desc())
        .limit(1)
    )
    return None if row is None else _version(row)


async def get_version(session: AsyncSession, user_id: uuid.UUID, number: int) -> Version:
    row = await session.scalar(
        select(ProfileVersionRow).where(
            ProfileVersionRow.user_id == user_id, ProfileVersionRow.number == number
        )
    )
    if row is None:
        raise VersionError(f"there's no profile version {number}")
    return _version(row)


async def list_versions(session: AsyncSession, user_id: uuid.UUID) -> list[VersionInfo]:
    """The user's versions, newest first, without their profiles."""
    rows = await session.execute(
        select(
            ProfileVersionRow.number,
            ProfileVersionRow.parent,
            ProfileVersionRow.author,
            ProfileVersionRow.change_set_id,
            ProfileVersionRow.restored_from,
            ProfileVersionRow.created_at,
        )
        .where(ProfileVersionRow.user_id == user_id)
        .order_by(ProfileVersionRow.number.desc())
    )
    return [
        VersionInfo(
            number=number,
            parent=parent,
            author=author,
            change_set_id=change_set_id,
            restored_from=restored_from,
            created_at=created_at,
        )
        for number, parent, author, change_set_id, restored_from, created_at in rows
    ]


async def restore_version(
    session: AsyncSession, user_id: uuid.UUID, number: int, *, author: Author = "owner"
) -> Version:
    """Save a copy of version `number` as the newest version. The versions since stay."""
    old = await get_version(session, user_id, number)
    return await save_version(session, user_id, old.profile, author=author, restored_from=number)


async def propose_change_set(
    session: AsyncSession, user_id: uuid.UUID, change_set: ChangeSet
) -> uuid.UUID:
    """Store a change set for review, with its operations in order."""
    row = ChangeSetRow(
        user_id=user_id,
        author=change_set.author,
        status=change_set.status,
        summary=change_set.summary,
        base_version=change_set.base_version,
    )
    session.add(row)
    await session.flush()
    for position, reviewed in enumerate(change_set.ops):
        session.add(
            ChangeOpRow(
                user_id=user_id,
                change_set_id=row.id,
                position=position,
                op=change_op_adapter.dump_python(reviewed.op, mode="json", by_alias=True),
                status=reviewed.status,
                reason=reviewed.reason,
                verifier_report=reviewed.verifier_report,
            )
        )
    await session.flush()
    return row.id


async def get_change_set(
    session: AsyncSession, user_id: uuid.UUID, change_set_id: uuid.UUID
) -> SavedChangeSet:
    row = await session.scalar(
        select(ChangeSetRow).where(
            ChangeSetRow.user_id == user_id, ChangeSetRow.id == change_set_id
        )
    )
    if row is None:
        raise VersionError(f"there's no change set {change_set_id}")
    ops = await session.scalars(
        select(ChangeOpRow)
        .where(ChangeOpRow.change_set_id == change_set_id)
        .order_by(ChangeOpRow.position)
    )
    change_set = ChangeSet(
        author=row.author,
        ops=tuple(
            ReviewedOp(
                op=change_op_adapter.validate_python(op.op),
                status=op.status,
                reason=op.reason,
                verifier_report=op.verifier_report,
            )
            for op in ops
        ),
        summary=row.summary,
        base_version=row.base_version,
        status=row.status,
    )
    return SavedChangeSet(
        id=row.id, created_at=row.created_at, decided_at=row.decided_at, change_set=change_set
    )


async def list_change_sets(
    session: AsyncSession, user_id: uuid.UUID, status: ChangeSetStatus | None = "proposed"
) -> list[SavedChangeSet]:
    """The user's change sets with `status` (all of them for None), oldest first."""
    query = select(ChangeSetRow.id).where(ChangeSetRow.user_id == user_id)
    if status is not None:
        query = query.where(ChangeSetRow.status == status)
    ids = await session.scalars(query.order_by(ChangeSetRow.created_at, ChangeSetRow.id))
    return [await get_change_set(session, user_id, change_set_id) for change_set_id in ids]


async def decide_op(
    session: AsyncSession,
    user_id: uuid.UUID,
    change_set_id: uuid.UUID,
    position: int,
    status: OpStatus,
    *,
    reason: str | None = None,
    op: ChangeOp | None = None,
    verifier_report: Mapping[str, JsonValue] | None = None,
) -> None:
    """Record the owner's decision on one operation (FR-REV-2). An edit replaces the operation
    and its verifier report, as the edited text is verified again."""
    values: dict[str, object] = {"status": status, "reason": reason}
    if op is not None:
        values |= {
            "op": change_op_adapter.dump_python(op, mode="json", by_alias=True),
            "verifier_report": None if verifier_report is None else dict(verifier_report),
        }
    result = await session.execute(
        update(ChangeOpRow)
        .where(
            ChangeOpRow.user_id == user_id,
            ChangeOpRow.change_set_id == change_set_id,
            ChangeOpRow.position == position,
        )
        .values(values)
        .returning(ChangeOpRow.id)
    )
    if result.scalar_one_or_none() is None:
        raise VersionError(f"change set {change_set_id} has no operation {position}")


async def close_change_set(
    session: AsyncSession, user_id: uuid.UUID, change_set_id: uuid.UUID, status: ChangeSetStatus
) -> None:
    """Mark a change set applied or rejected, once the owner has decided."""
    result = await session.execute(
        update(ChangeSetRow)
        .where(ChangeSetRow.user_id == user_id, ChangeSetRow.id == change_set_id)
        .values(status=status, decided_at=datetime.now(UTC))
        .returning(ChangeSetRow.id)
    )
    if result.scalar_one_or_none() is None:
        raise VersionError(f"there's no change set {change_set_id}")


def _version(row: ProfileVersionRow) -> Version:
    return Version(
        number=row.number,
        parent=row.parent,
        author=row.author,
        change_set_id=row.change_set_id,
        restored_from=row.restored_from,
        created_at=row.created_at,
        profile=Profile.model_validate(row.profile),
    )
