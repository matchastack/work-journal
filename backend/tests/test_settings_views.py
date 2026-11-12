"""What the Settings page shows of the reminders, and how it saves the style notes (T-048)."""

from datetime import UTC, datetime, time

import pytest
from pydantic import ValidationError

from app.reminders import DEFAULT_SCHEDULE, Schedule
from app.schema.settings import ReminderSettings, StyleNote, StyleNotes
from app.settings.views import (
    ReminderSchedule,
    ReminderView,
    StyleNotesChange,
    changed_notes,
    reminder_view,
)

ADDED = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
DEFAULT = ReminderSchedule(days=("fri",), time="18:00", time_zone="Asia/Singapore")


def test_reminders_left_unset_show_the_default_schedule() -> None:
    assert reminder_view(ReminderSettings(), DEFAULT_SCHEDULE) == ReminderView(
        schedule=DEFAULT,
        default=DEFAULT,
        chosen=False,
        paused=False,
        summary="Fridays at 18:00 (Asia/Singapore)",
    )


def test_what_the_owner_chose_replaces_the_default_and_the_rest_follows_it() -> None:
    stored = ReminderSettings(days=("mon", "thu"), time=time(7, 5), paused=True)
    view = reminder_view(stored, DEFAULT_SCHEDULE)
    assert view.schedule == ReminderSchedule(
        days=("mon", "thu"), time="07:05", time_zone="Asia/Singapore"
    )
    assert view.default == DEFAULT
    assert (view.chosen, view.paused) == (True, True)
    assert view.summary == "Mondays and Thursdays at 07:05 (Asia/Singapore)"


def test_the_app_default_comes_from_its_settings() -> None:
    default = Schedule(("sat", "sun"), time(9, 30), "Europe/London")
    view = reminder_view(ReminderSettings(time_zone="Asia/Tokyo"), default)
    assert view.default == ReminderSchedule(
        days=("sat", "sun"), time="09:30", time_zone="Europe/London"
    )
    assert view.schedule.time_zone == "Asia/Tokyo"
    assert view.chosen


def test_a_kept_note_keeps_where_it_came_from_and_a_new_one_is_the_owners() -> None:
    current = StyleNotes(
        avoid=("spearheaded",),
        notes=(
            StyleNote(text="Keep the team's name out", source="rejection", added_at=ADDED),
            StyleNote(text="Say what it fixed", source="owner", added_at=ADDED),
        ),
    )
    change = StyleNotesChange(
        avoid=("leveraged", "synergy"),
        notes=("keep the team's name out", "Prefer plain verbs"),
    )
    assert changed_notes(current, change, now=NOW) == StyleNotes(
        avoid=("leveraged", "synergy"),
        notes=(
            StyleNote(text="keep the team's name out", source="rejection", added_at=ADDED),
            StyleNote(text="Prefer plain verbs", source="owner", added_at=NOW),
        ),
    )


def test_the_saved_lists_hold_each_word_and_note_once() -> None:
    change = StyleNotesChange(
        avoid=("Leveraged", "leveraged", " synergy "),
        notes=("Say what it fixed", "say what it fixed"),
    )
    assert change.avoid == ("Leveraged", "synergy")
    assert change.notes == ("Say what it fixed",)


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ({"avoid": [""]}, "at least 1 character"),
        ({"avoid": ["x" * 101]}, "at most 100 characters"),
        ({"notes": ["x" * 501]}, "at most 500 characters"),
        ({"notes": [f"note {n}" for n in range(201)]}, "at most 200 items"),
    ],
)
def test_style_notes_that_cant_be_kept_are_refused(value: dict[str, object], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        StyleNotesChange.model_validate(value)
