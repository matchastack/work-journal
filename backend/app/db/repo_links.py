"""Linked GitHub repositories and the keys of the items read from them (FR-GH-2, FR-GH-7).

A link names the project its facts belong to and where its history starts. Each read keeps the
keys of the items it took (`repo_items`: a pull request's number or a commit's hash, never its
text), so no item is read twice. `save_part` keeps a part's keys and its facts in one
transaction, and saves nothing when another read took one of those items first.

Repository names, and a new project's name, are encrypted (FR-GH-9). A link is found by GitHub's
number for the repository, or by its name, compared once decrypted.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProfileVersionRow, RepoItemRow, RepoLink
from app.db.store import save_facts
from app.github.digest import Item, ItemKind
from app.github.extraction import RepoTarget
from app.schema.fact import Fact

LOOK_BACK = timedelta(days=14)
"""How far before the last read a read starts, so commits pushed late are still found (C7)."""
READ_LEASE = timedelta(minutes=30)
"""How long a read may work on a repository before another counts it as stopped, as
`UNFINISHED_AFTER` does for a refresh: well past the 300 s a refresh's job may run."""

ItemKey = tuple[ItemKind, str]
"""An item's kind and its number or hash: what a read keeps of it."""


@dataclass(frozen=True)
class LinkedRepository:
    id: uuid.UUID
    github_id: int
    name: str
    """`owner/name`, as GitHub last gave it."""
    target: RepoTarget
    since: date | None
    linked_at: datetime
    unlinked_at: datetime | None
    read_at: datetime | None
    reading_since: datetime | None = None
    """When the read working on it began, if one is."""

    @property
    def start(self) -> datetime | None:
        """Where the next read starts: 14 days before the last whole read began, or until one
        has finished, the month the history starts from (None: all of it)."""
        if self.read_at is not None:
            return self.read_at - LOOK_BACK
        if self.since is not None:
            return datetime(self.since.year, self.since.month, 1, tzinfo=UTC)
        return None


@dataclass(frozen=True)
class ItemCounts:
    read: int = 0
    failed: int = 0
    """Of those, the items in parts the model couldn't read."""


async def link_repository(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    github_id: int,
    name: str,
    target: RepoTarget,
    since: date | None,
) -> LinkedRepository:
    """Link the repository to `target`'s project. A repository linked before is linked again: its
    name, project and cap become these, and it keeps the items read and when it was last read,
    so reading carries on where it stopped (FR-GH-2). Its `since` changes only while no read has
    finished."""
    query = insert(RepoLink).values(
        user_id=user_id,
        github_id=github_id,
        name=name,
        project_id=target.project_id,
        new_project=target.new_project,
        ownership=target.ownership,
        since=since,
    )
    query = query.on_conflict_do_update(
        index_elements=[RepoLink.user_id, RepoLink.github_id],
        set_={
            "name": query.excluded.name,
            "project_id": query.excluded.project_id,
            "new_project": query.excluded.new_project,
            "ownership": query.excluded.ownership,
            # The existing row's: a read that finished already started from it.
            "since": case((RepoLink.read_at.is_(None), query.excluded.since), else_=RepoLink.since),
            "unlinked_at": None,
        },
    ).returning(RepoLink)
    row = await session.scalar(query, execution_options={"populate_existing": True})
    assert row is not None, "an upsert returns its row"
    return _linked(row)


async def get_link(
    session: AsyncSession, user_id: uuid.UUID, *, github_id: int
) -> LinkedRepository | None:
    row = await session.scalar(
        select(RepoLink).where(RepoLink.user_id == user_id, RepoLink.github_id == github_id)
    )
    return None if row is None else _linked(row)


async def list_links(
    session: AsyncSession, user_id: uuid.UUID, *, unlinked: bool = False
) -> list[LinkedRepository]:
    """The repositories the user linked, in the order they were linked; with `unlinked`, also
    those they unlinked."""
    query = select(RepoLink).where(RepoLink.user_id == user_id)
    if not unlinked:
        query = query.where(RepoLink.unlinked_at.is_(None))
    rows = await session.scalars(query.order_by(RepoLink.linked_at, RepoLink.id))
    return [_linked(row) for row in rows]


async def find_link(
    session: AsyncSession, user_id: uuid.UUID, name: str
) -> LinkedRepository | None:
    """The linked repository named `name`, as `owner/name` in any case. Names are encrypted, so
    they're compared once decrypted."""
    for link in await list_links(session, user_id):
        if link.name.casefold() == name.casefold():
            return link
    return None


async def unlink(session: AsyncSession, link_id: uuid.UUID, *, now: datetime) -> None:
    """Stop reading the repository. Its facts and the keys of the items read stay."""
    await session.execute(update(RepoLink).where(RepoLink.id == link_id).values(unlinked_at=now))


async def read_keys(session: AsyncSession, link_id: uuid.UUID) -> set[ItemKey]:
    """The items earlier reads of the repository took."""
    rows = await session.execute(
        select(RepoItemRow.kind, RepoItemRow.ref).where(RepoItemRow.link_id == link_id)
    )
    return {(kind, ref) for kind, ref in rows}


async def item_counts(session: AsyncSession, user_id: uuid.UUID) -> dict[uuid.UUID, ItemCounts]:
    """For each of the user's links, how many items were read, and how many of those were in
    parts the model couldn't read."""
    rows = await session.execute(
        select(
            RepoItemRow.link_id,
            func.count(),
            func.count().filter(RepoItemRow.failed),
        )
        .where(RepoItemRow.user_id == user_id)
        .group_by(RepoItemRow.link_id)
    )
    return {link_id: ItemCounts(read, failed) for link_id, read, failed in rows}


async def save_part(
    session: AsyncSession,
    user_id: uuid.UUID,
    link_id: uuid.UUID,
    items: Sequence[Item],
    facts: Sequence[Fact],
    *,
    failed: bool = False,
) -> bool:
    """Keep the keys of a part's items and its facts, and commit. Returns False, having saved
    nothing, when another read took one of the items first: that read keeps its own facts, so
    none is saved twice (FR-GH-7). `failed` records a part the model couldn't read."""
    rows = [
        {
            "user_id": user_id,
            "link_id": link_id,
            "kind": item.kind,
            "ref": item.ref,
            "failed": failed,
        }
        for item in items
    ]
    query = insert(RepoItemRow).values(rows).on_conflict_do_nothing()
    taken = (await session.execute(query.returning(RepoItemRow.ref))).all()
    if len(taken) < len(rows):
        await session.rollback()
        return False
    await save_facts(session, user_id, facts)
    await session.commit()
    return True


async def claim_read(session: AsyncSession, link_id: uuid.UUID, *, now: datetime) -> bool:
    """Mark that a read is working on the repository, and commit. Returns False, changing
    nothing, when another read is at work on it: that read takes what's new (T-071)."""
    claimed = await session.scalar(
        update(RepoLink)
        .where(
            RepoLink.id == link_id,
            (RepoLink.reading_since.is_(None)) | (RepoLink.reading_since < now - READ_LEASE),
        )
        .values(reading_since=now)
        .returning(RepoLink.id)
    )
    await session.commit()
    return claimed is not None


async def release_read(session: AsyncSession, link_id: uuid.UUID) -> None:
    """Mark that the read working on the repository is done, and commit."""
    await session.execute(update(RepoLink).where(RepoLink.id == link_id).values(reading_since=None))
    await session.commit()


async def finish_read(session: AsyncSession, link_id: uuid.UUID, started_at: datetime) -> None:
    """Record that a read which began at `started_at` took everything it found, and commit. The
    next read looks back from the latest such start."""
    await session.execute(
        update(RepoLink)
        .where(RepoLink.id == link_id)
        .values(read_at=func.greatest(func.coalesce(RepoLink.read_at, started_at), started_at))
    )
    await session.commit()


async def profile_loaded_at(session: AsyncSession, user_id: uuid.UUID) -> datetime | None:
    """When the user's first profile version was saved: when their profile was loaded."""
    return await session.scalar(
        select(ProfileVersionRow.created_at).where(
            ProfileVersionRow.user_id == user_id, ProfileVersionRow.number == 1
        )
    )


def _linked(row: RepoLink) -> LinkedRepository:
    return LinkedRepository(
        id=row.id,
        github_id=row.github_id,
        name=row.name,
        target=RepoTarget(row.project_id, row.new_project, row.ownership),
        since=row.since,
        linked_at=row.linked_at,
        unlinked_at=row.unlinked_at,
        read_at=row.read_at,
        reading_since=row.reading_since,
    )
