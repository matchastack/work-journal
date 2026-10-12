"""Reviewing proposed change sets (FR-REV-1..4).

`load_inbox` lists the change sets waiting for review, oldest first. Each operation comes with
what it does to the latest profile version (`app/inbox/describe.py`), the facts it cites, and its
verifier report.

The owner decides on each operation (FR-REV-2):
- accept or reject it, with an optional reason for a rejection, and change their mind until the
  change set is applied;
- edit the text of a new or reworded bullet. The edited text is checked by the verifier again
  and goes back to waiting for a decision, so text that fails is never accepted without the
  owner seeing its report (FR-FID-7);
- accept, at once, every operation still waiting whose verifier report passed. Flagged ones are
  left for the owner to decide one by one.

Once every operation is decided, `apply_change_set` applies the accepted ones to the latest
version, atomically, as one new version (FR-REV-3), and the reasons given for rejections become
style notes (FR-REV-4). A change set with nothing accepted is closed as rejected. Fact text is
decrypted only for the signed-in owner, and never logged.
"""

import asyncio
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Literal

from pydantic import JsonValue, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChangeSetRow, FactRow
from app.db.profiles import (
    SavedChangeSet,
    VersionError,
    close_change_set,
    decide_op,
    get_change_set,
    latest_version,
    list_change_sets,
    save_version,
)
from app.db.settings import add_style_notes, get_style_notes
from app.inbox.describe import DiffPart, Output, describe, word_diff
from app.llm.client import LLMClient
from app.profile_ops import ProfileOpError, apply
from app.schema.changes import AddBullet, Author, ChangeOp, EditBullet, OpStatus, ReviewedOp
from app.schema.common import Model
from app.schema.fact import Fact
from app.schema.profile import Profile
from app.schema.verification import VerifierReport
from app.telegram.processing import NO_PROFILE
from app.validate.verifier import Verifier

OpKind = Literal[
    "add_bullet",
    "edit_bullet",
    "set_bullet_status",
    "add_swap",
    "add_role",
    "add_education",
    "add_project",
    "update_field",
    "add_skill",
    "resolve_open_question",
]


class NotFound(LookupError):
    """The owner has no such change set, or it has no such operation."""


class ReviewError(ValueError):
    """The review can't go on as asked, e.g. the change set is already applied."""


class SourceFact(Model):
    """A fact an operation cites."""

    id: str
    statement: str | None
    """None when the fact has been deleted."""
    entry_id: str | None
    """The journal entry it came from; None for a fact from the imported resume."""


class InboxOp(Model):
    position: int
    kind: OpKind
    status: OpStatus
    reason: str | None
    """Why the owner rejected it."""
    title: str
    context: str | None
    before: str | None
    after: str | None
    diff: tuple[DiffPart, ...]
    """The text before and after, a word at a time, when the change rewords a text."""
    outputs: tuple[Output, ...]
    sources: tuple[SourceFact, ...]
    verifier_report: VerifierReport | None
    """None for an operation without generated text, such as benching a bullet."""
    editable: bool


class InboxChangeSet(Model):
    id: uuid.UUID
    author: Author
    summary: str | None
    base_version: int | None
    """The profile version the operations were proposed against."""
    created_at: datetime
    ops: tuple[InboxOp, ...]


class Inbox(Model):
    change_sets: tuple[InboxChangeSet, ...]
    """Oldest first."""
    profile_version: int | None
    """The latest profile version, which the changes apply to."""


async def load_inbox(session: AsyncSession, user_id: uuid.UUID) -> Inbox:
    """The change sets waiting for review."""
    version = await latest_version(session, user_id)
    profile = version.profile if version else NO_PROFILE
    saved = await list_change_sets(session, user_id, "proposed")
    cited = [fact_id for item in saved for op in item.change_set.ops for fact_id in op.op.fact_ids]
    facts = await _facts(session, user_id, cited)
    return Inbox(
        change_sets=tuple(_view(item, profile, facts) for item in saved),
        profile_version=version.number if version else None,
    )


async def load_change_set(
    session: AsyncSession, user_id: uuid.UUID, change_set_id: uuid.UUID
) -> InboxChangeSet:
    saved = await _get(session, user_id, change_set_id)
    version = await latest_version(session, user_id)
    cited = [fact_id for op in saved.change_set.ops for fact_id in op.op.fact_ids]
    facts = await _facts(session, user_id, cited)
    return _view(saved, version.profile if version else NO_PROFILE, facts)


async def decide(
    session: AsyncSession,
    user_id: uuid.UUID,
    change_set_id: uuid.UUID,
    position: int,
    status: OpStatus,
    reason: str | None = None,
) -> None:
    """Accept or reject an operation, or set it back to waiting. Only a rejection keeps a
    reason."""
    saved = await _open(session, user_id, change_set_id)
    _op(saved, position)
    await decide_op(
        session,
        user_id,
        change_set_id,
        position,
        status,
        reason=reason if status == "rejected" else None,
    )


async def edit_text(
    session: AsyncSession,
    user_id: uuid.UUID,
    change_set_id: uuid.UUID,
    position: int,
    text: str,
    client: LLMClient,
) -> VerifierReport:
    """Replace the text of a new or reworded bullet, check it with the verifier, and set the
    operation back to waiting for a decision. Returns the verifier's report."""
    saved = await _open(session, user_id, change_set_id)
    op = _op(saved, position).op
    if not isinstance(op, AddBullet | EditBullet):
        raise ReviewError("only the text of a new or reworded bullet can be edited")
    version = await latest_version(session, user_id)
    profile = version.profile if version else NO_PROFILE
    facts = await _facts(session, user_id, op.fact_ids)
    entries = {item.id for item in (*profile.work, *profile.education, *profile.projects)}
    if isinstance(op, AddBullet):
        edited: ChangeOp = op.model_copy(
            update={"bullet": op.bullet.model_copy(update={"text": text})}
        )
        parent, replaces = op.parent_id, None
    else:
        edited = op.model_copy(update={"text": text})
        parent, replaces = _parent_of(profile, op.bullet_id), op.bullet_id
    avoid = (await get_style_notes(session, user_id)).avoid
    verifier = Verifier(
        client=client,
        profile=profile,
        facts=[facts[fact_id] for fact_id in op.fact_ids if fact_id in facts],
        entry_id=parent if parent in entries else None,
        replaces=replaces,
        avoid=avoid,
    )
    report = await asyncio.to_thread(verifier.check, text)
    await decide_op(
        session,
        user_id,
        change_set_id,
        position,
        "proposed",
        op=edited,
        verifier_report=report.model_dump(mode="json"),
    )
    return report


async def accept_passed(session: AsyncSession, user_id: uuid.UUID, change_set_id: uuid.UUID) -> int:
    """Accept every operation still waiting whose text passed the verifier, or that has no text
    to verify (bulk accept). Returns how many were accepted; flagged ones keep waiting."""
    saved = await _open(session, user_id, change_set_id)
    accepted = 0
    for position, reviewed in enumerate(saved.change_set.ops):
        report = _report(reviewed.verifier_report)
        flagged = report is not None and not report.passed
        if reviewed.status == "proposed" and not flagged and not _unreadable(reviewed):
            await decide_op(session, user_id, change_set_id, position, "accepted")
            accepted += 1
    return accepted


async def apply_change_set(
    session: AsyncSession, user_id: uuid.UUID, change_set_id: uuid.UUID, *, now: datetime
) -> int | None:
    """Apply the accepted operations as one new profile version, and keep the reasons given for
    rejections as style notes. Returns the new version's number, or None when nothing was
    accepted."""
    saved = await _open(session, user_id, change_set_id)
    ops = saved.change_set.ops
    waiting = sum(1 for reviewed in ops if reviewed.status == "proposed")
    if waiting:
        raise ReviewError(f"decide on every change first: {waiting} still waiting")
    accepted = [reviewed.op for reviewed in ops if reviewed.status == "accepted"]
    number = None
    if accepted:
        latest = await latest_version(session, user_id)
        if latest is None:
            raise ReviewError("there's no profile to apply the changes to; load it first")
        try:
            profile = apply(latest.profile, accepted)
        except ProfileOpError as error:
            raise ReviewError(f"the changes don't fit your profile any more: {error}") from None
        author = saved.change_set.author
        version = await save_version(
            session, user_id, profile, author=author, change_set_id=change_set_id
        )
        number = version.number
    await close_change_set(session, user_id, change_set_id, "applied" if accepted else "rejected")
    reasons = [r.reason for r in ops if r.status == "rejected" and r.reason]
    await add_style_notes(session, user_id, reasons, source="rejection", now=now)
    return number


def _view(saved: SavedChangeSet, profile: Profile, facts: Mapping[str, Fact]) -> InboxChangeSet:
    change_set = saved.change_set
    return InboxChangeSet(
        id=saved.id,
        author=change_set.author,
        summary=change_set.summary,
        base_version=change_set.base_version,
        created_at=saved.created_at,
        ops=tuple(
            _op_view(position, reviewed, profile, facts)
            for position, reviewed in enumerate(change_set.ops)
        ),
    )


def _op_view(
    position: int, reviewed: ReviewedOp, profile: Profile, facts: Mapping[str, Fact]
) -> InboxOp:
    op = reviewed.op
    described = describe(op, profile)
    before, after = described.before, described.after
    return InboxOp(
        position=position,
        kind=op.op,
        status=reviewed.status,
        reason=reviewed.reason,
        title=described.title,
        context=described.context,
        before=before,
        after=after,
        diff=(
            word_diff(before, after)
            if described.reworded and before is not None and after is not None
            else ()
        ),
        outputs=described.outputs,
        sources=tuple(_source(fact_id, facts.get(fact_id)) for fact_id in op.fact_ids),
        verifier_report=_report(reviewed.verifier_report),
        editable=described.editable,
    )


def _source(fact_id: str, fact: Fact | None) -> SourceFact:
    if fact is None:
        return SourceFact(id=fact_id, statement=None, entry_id=None)
    return SourceFact(id=fact_id, statement=fact.statement, entry_id=fact.entry_id)


def _report(stored: Mapping[str, JsonValue] | None) -> VerifierReport | None:
    if stored is None:
        return None
    try:
        return VerifierReport.model_validate(stored)
    except ValidationError:
        return None


def _unreadable(reviewed: ReviewedOp) -> bool:
    """A stored report that isn't a verifier report can't vouch for the text."""
    return reviewed.verifier_report is not None and _report(reviewed.verifier_report) is None


def _parent_of(profile: Profile, bullet_id: str) -> str | None:
    for item in (*profile.work, *profile.education, *profile.projects):
        if any(bullet.id == bullet_id for bullet in item.highlights):
            return item.id
    return None


async def _facts(
    session: AsyncSession, user_id: uuid.UUID, fact_ids: Sequence[str]
) -> dict[str, Fact]:
    if not fact_ids:
        return {}
    rows = await session.scalars(
        select(FactRow.data).where(FactRow.user_id == user_id, FactRow.id.in_(set(fact_ids)))
    )
    facts = [Fact.model_validate_json(data) for data in rows]
    return {fact.id: fact for fact in facts}


async def _get(
    session: AsyncSession, user_id: uuid.UUID, change_set_id: uuid.UUID
) -> SavedChangeSet:
    try:
        return await get_change_set(session, user_id, change_set_id)
    except VersionError:
        raise NotFound(f"there's no change set {change_set_id}") from None


async def _open(
    session: AsyncSession, user_id: uuid.UUID, change_set_id: uuid.UUID
) -> SavedChangeSet:
    """The change set, locked for this decision, if it still waits for review."""
    await session.execute(
        select(ChangeSetRow.id)
        .where(ChangeSetRow.user_id == user_id, ChangeSetRow.id == change_set_id)
        .with_for_update()
    )
    saved = await _get(session, user_id, change_set_id)
    if saved.change_set.status != "proposed":
        raise ReviewError(f"this change set is already {saved.change_set.status}")
    return saved


def _op(saved: SavedChangeSet, position: int) -> ReviewedOp:
    ops = saved.change_set.ops
    if not 0 <= position < len(ops):
        raise NotFound(f"change set {saved.id} has no change {position}")
    return ops[position]
