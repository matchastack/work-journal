"""The owner's settings, each stored as JSON under its key in `settings` (`app/db/settings.py`).

Besides preferences, some keys keep state, such as whether the portfolio page is published."""

import re
from collections.abc import Sequence
from datetime import date, datetime
from datetime import time as clock_time
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, AwareDatetime, Field, StringConstraints, model_validator

from app.schema.common import Id, Model, NonEmptyStr, Visibility


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


HANDLE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
"""Lowercase letters and digits, with single hyphens between them: safe in a URL as it is."""
HANDLE_LENGTH = (2, 40)
MAX_ITEMS = 200
"""The most words, notes or terms one list may hold."""


def _handle(value: str) -> str:
    handle = value.lower()
    shortest, longest = HANDLE_LENGTH
    if not shortest <= len(handle) <= longest or not HANDLE.fullmatch(handle):
        raise ValueError(
            f"use {shortest} to {longest} letters, digits and single hyphens, starting and ending"
            " with a letter or digit, such as casey-example"
        )
    return handle


def once_each(values: Sequence[str]) -> tuple[str, ...]:
    """`values` in order, without a repeat that differs only in case."""
    seen: set[str] = set()
    kept: list[str] = []
    for value in values:
        if value.casefold() not in seen:
            seen.add(value.casefold())
            kept.append(value)
    return tuple(kept)


Handle = Annotated[str, AfterValidator(_handle)]
"""The portfolio page's address, `/p/<handle>`, in lowercase: `Casey-Example` is kept as
`casey-example`."""
Phrase = Annotated[str, StringConstraints(min_length=1, max_length=100)]
"""A word or a short phrase, such as a word to avoid or a confidential term."""
Phrases = Annotated[tuple[Phrase, ...], Field(max_length=MAX_ITEMS), AfterValidator(once_each)]
"""Words or phrases, each once whatever its case, in the order given."""


class PortfolioSettings(Model):
    """The public portfolio page's settings (FR-SET-3, FR-PRT-4, FR-PRT-5), under `portfolio`.

    The page (`app/portfolio/`) reads them each time it's shown, so a change shows at once.
    """

    handle: Handle | None = None
    """The page's address, `/p/<handle>`. None until the owner chooses one: there's no default,
    since the handle is still an open question (OQ-3). No two users have the same one."""
    default_visibility: Visibility = "public"
    """The visibility the page gives what the owner hasn't given a visibility of its own: the
    website and the online profiles. The email, the phone, the location and each bullet have
    their own (`app/schema/profile.py`). Only what's public shows on the page."""
    open_to_work: bool = False
    """Whether the page says the owner is open to work."""
    indexing: bool = True
    """Whether search engines may index the page. Off, the page asks them not to (noindex)."""


ResumeLabel = Annotated[str, StringConstraints(min_length=1, max_length=60)]
"""A download button's text on the portfolio page, such as `Resume`."""
MAX_RESUMES = 5
"""The most resumes the portfolio page offers."""


class PublishedResume(Model):
    """A resume visitors may download from the portfolio page (FR-PRT-7): the master resume or a
    tailored one, with the text of its button."""

    source: Literal["master", "application"]
    """`master`: the master resume the latest refresh rendered, which changes with each refresh.
    `application`: the tailored resume of one application."""
    application_id: Id | None = None
    """The application, for a tailored resume."""
    label: ResumeLabel

    @model_validator(mode="after")
    def names_its_application(self) -> Self:
        if (self.source == "application") != (self.application_id is not None):
            raise ValueError("a tailored resume names its application, and the master resume none")
        return self

    @property
    def key(self) -> str:
        """Which resume this is, whatever its label: `master` or `application:<id>`."""
        return "master" if self.application_id is None else f"application:{self.application_id}"


class PortfolioPublishing(Model):
    """Whether the portfolio page is published, and what it offers (FR-PRT-1, FR-PRT-3, FR-PRT-7),
    under `portfolio_publishing` (`app/portfolio/publishing.py`)."""

    published: bool = False
    version: int | None = Field(default=None, ge=1)
    """The profile version the page showed when it was published: the latest refresh's, or the
    newest before the first refresh. A later refresh's version replaces it."""
    published_at: AwareDatetime | None = None
    resumes: tuple[PublishedResume, ...] = Field(default=(), max_length=MAX_RESUMES)
    """The resumes visitors may download, in the order the page shows them. They're kept while
    the page is unpublished."""

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.published != (self.version is not None and self.published_at is not None):
            raise ValueError("a published page has a version and a time, and an unpublished none")
        keys = [resume.key for resume in self.resumes]
        if len(set(keys)) != len(keys):
            raise ValueError("each resume can be offered once")
        return self


class ConfidentialTerms(Model):
    """Words and names that must never appear in public outputs: the portfolio page, published
    PDFs and the LinkedIn pack (FR-SET-5, NFR-PRIV-4), under `confidential_terms`.

    T-049 blocks them, matching whole words in any case. Until then they're only kept.
    """

    terms: Phrases = ()
