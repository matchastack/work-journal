"""The owner's settings: one JSON value per key and user (`Setting` in `app/db/models.py`)."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting
from app.schema.settings import StyleNote, StyleNotes

STYLE_NOTES = "style_notes"


async def get_style_notes(session: AsyncSession, user_id: uuid.UUID) -> StyleNotes:
    """The user's style notes; none until they have some."""
    value = await session.scalar(
        select(Setting.value).where(Setting.user_id == user_id, Setting.key == STYLE_NOTES)
    )
    return StyleNotes() if value is None else StyleNotes.model_validate(value)


async def save_style_notes(session: AsyncSession, user_id: uuid.UUID, notes: StyleNotes) -> None:
    value = notes.model_dump(mode="json", by_alias=True)
    query = insert(Setting).values(user_id=user_id, key=STYLE_NOTES, value=value)
    await session.execute(
        query.on_conflict_do_update(
            index_elements=[Setting.user_id, Setting.key],
            set_={"value": query.excluded.value, "updated_at": func.now()},
        )
    )


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
