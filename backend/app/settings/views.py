"""What the Settings page shows and changes (FR-SET-1 to FR-SET-5).

The preferences are kept under their keys in `settings` (`app/db/settings.py`). The page reads
them all at once, and saves one group at a time: the reminders, the portfolio page, the style
notes or the confidential terms. The Telegram link is made by `/api/telegram/link`
(`app/telegram/routes.py`).
"""

import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import AfterValidator, Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.settings import (
    get_confidential_terms,
    get_portfolio_settings,
    get_reminder_settings,
    get_style_notes,
)
from app.reminders import Schedule, describe, effective
from app.schema.common import Model
from app.schema.settings import (
    MAX_ITEMS,
    ConfidentialTerms,
    Days,
    LocalTime,
    Phrases,
    PortfolioSettings,
    ReminderSettings,
    StyleNote,
    StyleNotes,
    TimeZone,
    Weekday,
    once_each,
)
from app.telegram.linking import linked_at

NoteText = Annotated[str, StringConstraints(min_length=1, max_length=500)]
"""A style note: a preference about wording, in a sentence or two."""


class TelegramView(Model):
    """Whether the owner's Telegram chat is linked, and since when (FR-SET-1)."""

    linked: bool
    linked_at: datetime | None


class ReminderSchedule(Model):
    """When reminders go out: on `days`, at `time` (`HH:MM`) in `time_zone`."""

    days: tuple[Weekday, ...]
    time: str
    time_zone: str

    @classmethod
    def of(cls, schedule: Schedule) -> Self:
        return cls(days=schedule.days, time=f"{schedule.time:%H:%M}", time_zone=schedule.time_zone)


class ReminderView(Model):
    """The owner's reminders (FR-SET-2): the schedule they follow, which is the app's default
    for what they haven't chosen, and whether they're paused."""

    schedule: ReminderSchedule
    default: ReminderSchedule
    """The app's schedule (`REMINDER_DAYS`, `REMINDER_TIME` and `REMINDER_TIME_ZONE`)."""
    chosen: bool
    """Whether the owner chose any of the days, the time or the time zone."""
    paused: bool
    summary: str
    """The schedule in words, such as `Fridays at 18:00 (Asia/Singapore)`."""


class SettingsView(Model):
    """Everything the Settings page shows."""

    telegram: TelegramView
    reminders: ReminderView
    portfolio: PortfolioSettings
    style_notes: StyleNotes
    confidential_terms: ConfidentialTerms


class ReminderChange(Model):
    """The reminder schedule the Settings page saves (FR-SET-2). A day, time or time zone left
    null follows the app's default."""

    days: Days | None = None
    time: LocalTime | None = None
    time_zone: TimeZone | None = None
    paused: bool | None = None
    """Null keeps the pause as it is now, so a `/pause` sent while the page was open isn't
    undone."""


class StyleNotesChange(Model):
    """The owner's style notes as the page saves them (FR-SET-4): every word to avoid and every
    note, each once whatever its case."""

    avoid: Phrases = ()
    notes: Annotated[
        tuple[NoteText, ...], Field(max_length=MAX_ITEMS), AfterValidator(once_each)
    ] = ()


def reminder_view(stored: ReminderSettings, default: Schedule) -> ReminderView:
    schedule = effective(stored, default)
    return ReminderView(
        schedule=ReminderSchedule.of(schedule),
        default=ReminderSchedule.of(default),
        chosen=any(value is not None for value in (stored.days, stored.time, stored.time_zone)),
        paused=schedule.paused,
        summary=describe(schedule),
    )


def changed_notes(current: StyleNotes, change: StyleNotesChange, *, now: datetime) -> StyleNotes:
    """The style notes once `change` is saved. A note the owner kept keeps where it came from,
    such as a rejected change (FR-REV-4), and when it was added; a new one is the owner's, from
    `now`."""
    known = {note.text.casefold(): note for note in current.notes}
    notes: list[StyleNote] = []
    for text in change.notes:
        before = known.get(text.casefold())
        if before is None:
            notes.append(StyleNote(text=text, source="owner", added_at=now))
        else:
            notes.append(before.model_copy(update={"text": text}))
    return StyleNotes(avoid=change.avoid, notes=tuple(notes))


async def load_settings(
    session: AsyncSession, user_id: uuid.UUID, *, default_reminders: Schedule
) -> SettingsView:
    """Every preference of the user's, with `default_reminders` for what they haven't chosen."""
    since = await linked_at(session, user_id)
    return SettingsView(
        telegram=TelegramView(linked=since is not None, linked_at=since),
        reminders=reminder_view(await get_reminder_settings(session, user_id), default_reminders),
        portfolio=await get_portfolio_settings(session, user_id),
        style_notes=await get_style_notes(session, user_id),
        confidential_terms=await get_confidential_terms(session, user_id),
    )
