"""The owner's reminder setting and the bot's reminder record (FR-REM-1, FR-SET-2)."""

from datetime import UTC, date, datetime, time

import pytest
from pydantic import ValidationError

from app.schema.settings import ReminderSettings, ReminderState


def test_pausing_alone_is_a_whole_setting() -> None:
    """`/pause` stores only the pause: the schedule keeps following the app's defaults."""
    reminders = ReminderSettings.model_validate({"paused": True})
    assert reminders == ReminderSettings(paused=True)
    assert (reminders.days, reminders.time, reminders.time_zone) == (None, None, None)


def test_a_schedule_is_stored_in_camel_case_with_days_in_week_order() -> None:
    reminders = ReminderSettings.model_validate(
        {"days": ["thu", "mon", "thu"], "time": "07:30", "timeZone": "Europe/London"}
    )
    assert reminders.days == ("mon", "thu")
    assert reminders.time == time(7, 30)
    assert reminders.model_dump(mode="json") == {
        "days": ["mon", "thu"],
        "time": "07:30:00",
        "timeZone": "Europe/London",
        "paused": False,
    }


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ({"days": []}, "at least 1 item"),
        ({"days": ["friday"]}, "Input should be 'mon'"),
        ({"time": "18:00+08:00"}, "must be a local time"),
        ({"time": "18:00:30"}, "in hours and minutes"),
        ({"timeZone": "Mars/Olympus_Mons"}, "not a time zone"),
        ({"timeZone": ""}, "not a time zone"),
        ({"pausd": True}, "Extra inputs"),
    ],
)
def test_a_setting_that_cant_be_followed_is_rejected(value: dict[str, object], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        ReminderSettings.model_validate(value)


def test_the_reminder_record_round_trips_through_json() -> None:
    state = ReminderState(
        last_slot=datetime(2026, 10, 16, 10, 0, tzinfo=UTC),
        message_id=42,
        snoozed_until=datetime(2026, 10, 17, 10, 0, tzinfo=UTC),
        nothing_weeks=(date(2026, 10, 12),),
    )
    assert ReminderState.model_validate_json(state.model_dump_json()) == state
    assert ReminderState() == ReminderState(
        last_slot=None, message_id=None, snoozed_until=None, nothing_weeks=()
    )


def test_the_record_keeps_times_with_their_time_zone() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        ReminderState.model_validate({"lastSlot": "2026-10-16T18:00:00"})
