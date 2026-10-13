"""The owner's settings, each stored as JSON under its key in `settings` (`app/db/settings.py`)."""

from datetime import datetime
from typing import Literal

from app.schema.common import Model, NonEmptyStr


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
