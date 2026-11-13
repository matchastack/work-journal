"""The owner's settings: one JSON value per key and user (`Setting` in `app/db/models.py`).

Besides preferences, three keys keep the LinkedIn pack's state (T-024, T-061): the pack last
written, the snapshot of the sections last marked done, and the sections of a pack a refresh is
writing, kept as each is written. `reminders` holds the owner's reminder schedule and pause, and
`reminder_state` the bot's record of the reminders it sent or skipped (T-035) and of the open
questions they asked (T-036). `portfolio` holds the portfolio page's settings, and
`portfolio_publishing` whether it's published and what it offers (T-046). `confidential_terms`
holds the terms public outputs never name (T-049). The Settings page (`app/settings/`) edits the
preferences, and the Portfolio page publishing.
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
from app.schema.settings import (
    ConfidentialTerms,
    PortfolioPublishing,
    PortfolioSettings,
    ReminderSettings,
    ReminderState,
    StyleNote,
    StyleNotes,
)

STYLE_NOTES = "style_notes"
LINKEDIN_PACK = "linkedin_pack"
LINKEDIN_DONE = "linkedin_done"
LINKEDIN_DRAFT = "linkedin_draft"
REMINDERS = "reminders"
REMINDER_STATE = "reminder_state"
PORTFOLIO = "portfolio"
PORTFOLIO_PUBLISHING = "portfolio_publishing"
CONFIDENTIAL_TERMS = "confidential_terms"
HANDLE_LOCK = 0x776A68616E646C65
"""The advisory lock taken while a portfolio handle is checked and saved: "wjhandle" in ASCII."""


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
    confidential: tuple[str, ...]
    """The owner's confidential terms, which no section may name (NFR-PRIV-4)."""


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
    latest application, their style notes and their confidential terms. `wj linkedin --db` and a
    refresh read the same."""
    done = await get_linkedin_done(session, user_id)
    applications = await list_applications(session, user_id)
    notes = await get_style_notes(session, user_id)
    return PackInputs(
        done=done,
        latest_application=applications[0] if applications else None,
        avoid=notes.avoid,
        preferences=tuple(note.text for note in notes.notes),
        confidential=(await get_confidential_terms(session, user_id)).terms,
    )


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


async def get_portfolio_settings(session: AsyncSession, user_id: uuid.UUID) -> PortfolioSettings:
    """The user's portfolio settings; until they set any, the defaults, without a handle."""
    value = await _get(session, user_id, PORTFOLIO)
    return PortfolioSettings() if value is None else PortfolioSettings.model_validate(value)


async def save_portfolio_settings(
    session: AsyncSession, user_id: uuid.UUID, portfolio: PortfolioSettings
) -> None:
    """Replace the user's portfolio settings. Check `handle_taken` first, in the same
    transaction: no two users may have the same handle."""
    await _save(session, user_id, PORTFOLIO, portfolio)


async def handle_taken(session: AsyncSession, user_id: uuid.UUID, handle: str) -> bool:
    """Whether another user's portfolio page has the handle `handle`. It takes a lock that's
    held until the transaction ends, so that two users can't both take one handle."""
    await session.execute(select(func.pg_advisory_xact_lock(HANDLE_LOCK)))
    owner = await session.scalar(
        select(Setting.user_id)
        .where(
            Setting.key == PORTFOLIO,
            Setting.value["handle"].astext == handle,
            Setting.user_id != user_id,
        )
        .limit(1)
    )
    return owner is not None


async def portfolio_owner(session: AsyncSession, handle: str) -> uuid.UUID | None:
    """The user whose portfolio page has the handle `handle`, if one has. None, too, if somehow
    two have it: a page is never shown for the wrong user."""
    owners = (
        await session.scalars(
            select(Setting.user_id)
            .where(Setting.key == PORTFOLIO, Setting.value["handle"].astext == handle)
            .limit(2)
        )
    ).all()
    return owners[0] if len(owners) == 1 else None


async def get_portfolio_publishing(
    session: AsyncSession, user_id: uuid.UUID
) -> PortfolioPublishing:
    """Whether the user's portfolio page is published; until they publish it, it isn't."""
    value = await _get(session, user_id, PORTFOLIO_PUBLISHING)
    return PortfolioPublishing() if value is None else PortfolioPublishing.model_validate(value)


async def save_portfolio_publishing(
    session: AsyncSession, user_id: uuid.UUID, publishing: PortfolioPublishing
) -> None:
    await _save(session, user_id, PORTFOLIO_PUBLISHING, publishing)


async def get_confidential_terms(session: AsyncSession, user_id: uuid.UUID) -> ConfidentialTerms:
    """The user's confidential terms; none until they add some."""
    value = await _get(session, user_id, CONFIDENTIAL_TERMS)
    return ConfidentialTerms() if value is None else ConfidentialTerms.model_validate(value)


async def save_confidential_terms(
    session: AsyncSession, user_id: uuid.UUID, terms: ConfidentialTerms
) -> None:
    await _save(session, user_id, CONFIDENTIAL_TERMS, terms)


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
