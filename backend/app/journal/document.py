"""The journal as one document (FR-JRN-5): every entry, oldest first, with its messages, their
earlier text (FR-JRN-3) and the facts saved from it.

It's read in a few queries, however long the journal is, so the page and the export stay fast
(FR-JRN-7). Each fact lists the bullets in the latest profile version that cite it, so the web
app can warn before it's changed or deleted (FR-JRN-6). Text is decrypted only for the signed-in
owner, and never logged.
"""

import uuid
from collections import defaultdict
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FactRow, JournalEntry, JournalMessage, JournalMessageEdit
from app.db.profiles import latest_version
from app.schema.common import Model
from app.schema.fact import Fact
from app.schema.profile import Profile
from app.telegram.processing import NO_PROFILE
from app.telegram.roles import role_label

EntryStatus = Literal["open", "waiting_for_role", "extracting", "processed", "failed"]
"""Where an entry is in capture (FR-CAP-4, FR-CAP-11, FR-CAP-5): still taking messages, closed
and waiting for the owner to choose its role, waiting for its facts, done, or given up on."""


class MessageEdit(Model):
    """A message's text before an edit."""

    text: str
    replaced_at: datetime


class JournalMessageView(Model):
    id: uuid.UUID
    sent_at: datetime
    edited_at: datetime | None
    text: str
    """The text as last edited."""
    edits: tuple[MessageEdit, ...]
    """The text before each edit, oldest first."""


class Citation(Model):
    """A bullet in the latest profile version that cites a fact."""

    bullet_id: str
    text: str


class JournalFact(Fact):
    cited_by: tuple[Citation, ...] = ()


class JournalEntryView(Model):
    id: uuid.UUID
    opened_at: datetime
    closed_at: datetime | None
    status: EntryStatus
    triage: Literal["work", "other"] | None
    """Whether a processed entry is about work; only work gives facts (FR-CAP-8)."""
    role_id: str | None
    """The role in the profile that the owner chose for the entry."""
    new_role: str | None
    """The name of a new role that the owner gave the entry instead."""
    messages: tuple[JournalMessageView, ...]
    facts: tuple[JournalFact, ...]


class RoleOption(Model):
    id: str
    label: str
    """E.g. `Engineer, Northwind Traders (2025-now)`, as the bot shows it."""


class ProjectOption(Model):
    id: str
    name: str


class Journal(Model):
    entries: tuple[JournalEntryView, ...]
    """Oldest first."""
    roles: tuple[RoleOption, ...]
    """The roles in the latest profile version, which facts can link to."""
    projects: tuple[ProjectOption, ...]


def entry_status(entry: JournalEntry) -> EntryStatus:
    if entry.closed_at is None:
        return "open"
    if entry.processed_at is not None:
        return "processed"
    if entry.failed_at is not None:
        return "failed"
    if entry.role_id is None and entry.new_role is None:
        return "waiting_for_role"
    return "extracting"


def citations(profile: Profile) -> dict[str, list[Citation]]:
    """The bullets that cite each fact, by fact ID."""
    cited: dict[str, list[Citation]] = defaultdict(list)
    for bullet in profile.all_bullets():
        for fact_id in dict.fromkeys(bullet.sources):
            cited[fact_id].append(Citation(bullet_id=bullet.id, text=bullet.text))
    return cited


async def latest_profile(session: AsyncSession, user_id: uuid.UUID) -> Profile:
    """The latest profile version, or `NO_PROFILE` before one is loaded."""
    version = await latest_version(session, user_id)
    return NO_PROFILE if version is None else version.profile


async def load_journal(session: AsyncSession, user_id: uuid.UUID) -> Journal:
    """The user's whole journal."""
    entries = await session.scalars(
        select(JournalEntry)
        .where(JournalEntry.user_id == user_id)
        .order_by(JournalEntry.opened_at, JournalEntry.id)
    )
    edits: dict[uuid.UUID, list[MessageEdit]] = defaultdict(list)
    for message_id, text, replaced_at in await session.execute(
        select(
            JournalMessageEdit.message_id, JournalMessageEdit.text, JournalMessageEdit.replaced_at
        )
        .where(JournalMessageEdit.user_id == user_id)
        .order_by(JournalMessageEdit.replaced_at, JournalMessageEdit.id)
    ):
        edits[message_id].append(MessageEdit(text=text, replaced_at=replaced_at))
    messages: dict[uuid.UUID | None, list[JournalMessageView]] = defaultdict(list)
    for message_id, entry_id, text, sent_at, edited_at in await session.execute(
        select(
            JournalMessage.id,
            JournalMessage.entry_id,
            JournalMessage.text,
            JournalMessage.sent_at,
            JournalMessage.edited_at,
        )
        .where(
            JournalMessage.user_id == user_id,
            JournalMessage.sender == "owner",
            JournalMessage.entry_id.is_not(None),
        )
        .order_by(JournalMessage.sent_at, JournalMessage.id)
    ):
        messages[entry_id].append(
            JournalMessageView(
                id=message_id,
                sent_at=sent_at,
                edited_at=edited_at,
                text=text,
                edits=tuple(edits[message_id]),
            )
        )
    profile = await latest_profile(session, user_id)
    cited = citations(profile)
    facts: dict[str | None, list[JournalFact]] = defaultdict(list)
    for entry_id, data in await session.execute(
        select(FactRow.entry_id, FactRow.data)
        .where(
            FactRow.user_id == user_id,
            FactRow.origin == "journal",
            FactRow.entry_id.is_not(None),
        )
        # IDs end in each fact's number in its entry: `<entry>_2` comes before `<entry>_10`.
        .order_by(FactRow.created_at, FactRow.entry_id, func.length(FactRow.id), FactRow.id)
    ):
        fact = Fact.model_validate_json(data)
        facts[entry_id].append(journal_fact(fact, cited))
    return Journal(
        entries=tuple(
            JournalEntryView(
                id=entry.id,
                opened_at=entry.opened_at,
                closed_at=entry.closed_at,
                status=entry_status(entry),
                triage=entry.triage,
                role_id=entry.role_id,
                new_role=entry.new_role,
                messages=tuple(messages[entry.id]),
                facts=tuple(facts[str(entry.id)]),
            )
            for entry in entries
        ),
        roles=tuple(RoleOption(id=role.id, label=role_label(role)) for role in profile.work),
        projects=tuple(
            ProjectOption(id=project.id, name=project.name) for project in profile.projects
        ),
    )


def journal_fact(fact: Fact, cited: dict[str, list[Citation]]) -> JournalFact:
    return JournalFact.model_validate(
        {**fact.model_dump(by_alias=True), "citedBy": cited.get(fact.id, ())}
    )
