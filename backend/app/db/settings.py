"""The owner's settings: one JSON value per key and user (`Setting` in `app/db/models.py`).

Besides preferences, two keys keep the LinkedIn pack's state (T-024): the pack last written, and
the snapshot of the sections last marked done. `reminders` holds the owner's reminder schedule
and pause, and `reminder_state` the bot's record of the reminders it sent or skipped (T-035).
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting
from app.linkedin import LinkedInDone, LinkedInPack
from app.schema.common import Model
from app.schema.settings import ReminderSettings, ReminderState, StyleNote, StyleNotes

STYLE_NOTES = "style_notes"
LINKEDIN_PACK = "linkedin_pack"
LINKEDIN_DONE = "linkedin_done"
REMINDERS = "reminders"
REMINDER_STATE = "reminder_state"


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


async def get_reminder_settings(session: AsyncSession, user_id: uuid.UUID) -> ReminderSettings:
    """The user's reminder schedule and pause; until they set any, the app's defaults, unpaused."""
    value = await _get(session, user_id, REMINDERS)
    return ReminderSettings() if value is None else ReminderSettings.model_validate(value)


async def save_reminder_settings(
    session: AsyncSession, user_id: uuid.UUID, reminders: ReminderSettings
) -> None:
    """Replace the user's reminder setting. Read and change it while holding the lock that
    `app/telegram/reminders.py` takes, the owner's `telegram_links` row, so that a change can't
    undo a `/pause` made at the same moment."""
    await _save(session, user_id, REMINDERS, reminders)


async def get_reminder_state(session: AsyncSession, user_id: uuid.UUID) -> ReminderState:
    """The bot's record of the user's reminders; empty before the first."""
    value = await _get(session, user_id, REMINDER_STATE)
    return ReminderState() if value is None else ReminderState.model_validate(value)


async def save_reminder_state(
    session: AsyncSession, user_id: uuid.UUID, state: ReminderState
) -> None:
    await _save(session, user_id, REMINDER_STATE, state)


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
