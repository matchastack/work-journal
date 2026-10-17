"""The owner's settings, each stored as JSON under its key in `settings` (`app/db/settings.py`)."""

from datetime import date, datetime
from datetime import time as clock_time
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, AwareDatetime, Field

from app.schema.common import Id, Model, NonEmptyStr


class StyleNote(Model):
    """A preference about wording. A reason the owner gave for rejecting a proposed change
    becomes one (FR-REV-4)."""

    text: NonEmptyStr
    source: Literal["owner", "rejection"] = "owner"
    added_at: datetime


class StyleNotes(Model):
    """The owner's style notes, which apply to every writing style (FR-WRT-4, FR-SET-4)."""

    avoid: tuple[NonEmptyStr, ...] = ()
    """Words and phrases to avoid. The style check flags them (T-013)."""
    notes: tuple[StyleNote, ...] = ()
    """Preferences, oldest first."""


Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: tuple[Weekday, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
"""Monday first, as `date.weekday()` numbers them."""


def _in_week_order(days: tuple[Weekday, ...]) -> tuple[Weekday, ...]:
    return tuple(day for day in WEEKDAYS if day in days)


def _local_minute(value: clock_time) -> clock_time:
    if value.tzinfo is not None:
        raise ValueError("must be a local time, without a time zone")
    if value.second or value.microsecond:
        raise ValueError("must be in hours and minutes, such as 18:00")
    return value


def _known_zone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError):  # OSError: a folder, such as "Asia"
        message = f"not a time zone: {value!r}; use an IANA name such as Asia/Singapore"
        raise ValueError(message) from None
    return value


Days = Annotated[tuple[Weekday, ...], Field(min_length=1), AfterValidator(_in_week_order)]
"""Days of the week, at least one, each once and Monday first."""
LocalTime = Annotated[clock_time, AfterValidator(_local_minute)]
"""A time of day in hours and minutes, in a time zone given beside it."""
TimeZone = Annotated[str, AfterValidator(_known_zone)]
"""An IANA time zone, such as `Asia/Singapore`."""


class ReminderSettings(Model):
    """The owner's reminder schedule and pause (FR-REM-1, FR-SET-2), under `reminders`.

    A field left unset takes the app's default (`REMINDER_DAYS`, `REMINDER_TIME` and
    `REMINDER_TIME_ZONE`), so a change to those reaches every user who hasn't chosen their own.
    """

    days: Days | None = None
    time: LocalTime | None = None
    time_zone: TimeZone | None = None
    paused: bool = False
    """Set by `/pause` and cleared by `/resume` (FR-REM-3)."""


class Answering(Model):
    """The open question whose answer the owner's next message is (FR-EXT-4)."""

    question_id: Id
    since: AwareDatetime
    """When the owner pressed "Answer the question": the wait lapses after the entry timeout."""


class ReminderState(Model):
    """The bot's record of the owner's reminders, under `reminder_state` (`app/reminders.py`)."""

    last_slot: AwareDatetime | None = None
    """The latest reminder time dealt with: sent, or skipped. None before the first."""
    message_id: int | None = None
    """The newest reminder message, whose buttons still work, until one is pressed."""
    message_slot: AwareDatetime | None = None
    """The reminder time that message was for: "Nothing this week" means its week."""
    snoozed_until: AwareDatetime | None = None
    """When "Remind me tomorrow" brings the reminder back, if it was pressed."""
    nothing_weeks: tuple[date, ...] = ()
    """The weeks of the reminders whose "Nothing this week" was pressed (FR-REM-3), as their
    Mondays in the owner's time zone, oldest first."""
    message_question: Id | None = None
    """The open question the newest reminder message asks (FR-REM-5), which its "Answer the
    question" button answers, until one of its buttons is pressed."""
    asked: tuple[Id, ...] = ()
    """The open questions asked in the current round, oldest first (`app/open_questions.py`)."""
    answering: Answering | None = None
    """The question whose "Answer the question" was pressed, until the owner's next message
    takes the answer or the wait lapses."""
