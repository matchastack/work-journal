"""The owner's settings: one JSON value per key and user (`Setting` in `app/db/models.py`).

Besides preferences, three keys keep the LinkedIn pack's state (T-024, T-061): the pack last
written, the snapshot of the sections last marked done, and the sections of a pack a refresh is
writing, kept as each is written.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting
from app.db.store import list_applications
from app.linkedin import LinkedInDone, LinkedInPack
from app.schema.common import Model
from app.schema.jobs import Application
from app.schema.settings import StyleNote, StyleNotes

STYLE_NOTES = "style_notes"
LINKEDIN_PACK = "linkedin_pack"
LINKEDIN_DONE = "linkedin_done"
LINKEDIN_DRAFT = "linkedin_draft"


@dataclass(frozen=True)
class PackInputs:
    """What the LinkedIn pack is written from, besides the profile (`app/linkedin.py`)."""

    done: LinkedInDone | None
    """The sections last marked done."""
    latest_application: Application | None
    avoid: tuple[str, ...]
    """The owner's words to avoid (FR-WRT-4)."""
    preferences: tuple[str, ...]
    """The owner's style notes."""


async def get_style_notes(session: AsyncSession, user_id: uuid.UUID) -> StyleNotes:
    """The user's style notes; none until they have some."""
    value = await _get(session, user_id, STYLE_NOTES)
    return StyleNotes() if value is None else StyleNotes.model_validate(value)


async def save_style_notes(session: AsyncSession, user_id: uuid.UUID, notes: StyleNotes) -> None:
    await _save(session, user_id, STYLE_NOTES, notes)


async def add_style_notes(
    session: AsyncSession,
    user_id: uuid.UUID,
    texts: Sequence[str],
    *,
    source: Literal["owner", "rejection"],
    now: datetime,
) -> None:
    """Add a note for each of `texts`, after the user's others. A note they have already is
    left as it is."""
    current = await get_style_notes(session, user_id)
    known = {note.text.casefold() for note in current.notes}
    added: list[StyleNote] = []
    for text in texts:
        note = StyleNote(text=text, source=source, added_at=now)
        if note.text.casefold() not in known:
            known.add(note.text.casefold())
            added.append(note)
    if added:
        notes = current.model_copy(update={"notes": (*current.notes, *added)})
        await save_style_notes(session, user_id, notes)


async def get_linkedin_pack(session: AsyncSession, user_id: uuid.UUID) -> LinkedInPack | None:
    """The LinkedIn pack last written; None before the first."""
    value = await _get(session, user_id, LINKEDIN_PACK)
    return None if value is None else LinkedInPack.model_validate(value)


async def save_linkedin_pack(session: AsyncSession, user_id: uuid.UUID, pack: LinkedInPack) -> None:
    await _save(session, user_id, LINKEDIN_PACK, pack)


async def get_linkedin_done(session: AsyncSession, user_id: uuid.UUID) -> LinkedInDone | None:
    """The sections last marked done; None until the owner first marks a pack done."""
    value = await _get(session, user_id, LINKEDIN_DONE)
    return None if value is None else LinkedInDone.model_validate(value)


async def save_linkedin_done(session: AsyncSession, user_id: uuid.UUID, done: LinkedInDone) -> None:
    await _save(session, user_id, LINKEDIN_DONE, done)


async def get_linkedin_draft(session: AsyncSession, user_id: uuid.UUID) -> LinkedInPack | None:
    """The sections of a pack a refresh is writing, as far as it got (`app/jobs.py`); None when
    none is being written."""
    value = await _get(session, user_id, LINKEDIN_DRAFT)
    return None if value is None else LinkedInPack.model_validate(value)


async def save_linkedin_draft(
    session: AsyncSession, user_id: uuid.UUID, draft: LinkedInPack
) -> None:
    await _save(session, user_id, LINKEDIN_DRAFT, draft)


async def clear_linkedin_draft(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        delete(Setting).where(Setting.user_id == user_id, Setting.key == LINKEDIN_DRAFT)
    )


async def linkedin_pack_inputs(session: AsyncSession, user_id: uuid.UUID) -> PackInputs:
    """What the user's LinkedIn pack is written from: the sections last marked done, their
    latest application, and their style notes. `wj linkedin --db` and a refresh read the same."""
    done = await get_linkedin_done(session, user_id)
    applications = await list_applications(session, user_id)
    notes = await get_style_notes(session, user_id)
    return PackInputs(
        done=done,
        latest_application=applications[0] if applications else None,
        avoid=notes.avoid,
        preferences=tuple(note.text for note in notes.notes),
    )


async def _get(session: AsyncSession, user_id: uuid.UUID, key: str) -> dict[str, object] | None:
    return await session.scalar(
        select(Setting.value).where(Setting.user_id == user_id, Setting.key == key)
    )


async def _save(session: AsyncSession, user_id: uuid.UUID, key: str, value: Model) -> None:
    data = value.model_dump(mode="json", by_alias=True)
    query = insert(Setting).values(user_id=user_id, key=key, value=data)
    await session.execute(
        query.on_conflict_do_update(
            index_elements=[Setting.user_id, Setting.key],
            set_={"value": query.excluded.value, "updated_at": func.now()},
        )
    )
