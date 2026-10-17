"""When reminders go out (FR-REM-1, FR-REM-2, FR-REM-3, C6), with a frozen clock: every `now` is
given."""

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.reminders import (
    DEFAULT_SCHEDULE,
    Due,
    Outcome,
    Schedule,
    deadline,
    default_schedule,
    describe,
    due,
    effective,
    latest_slot,
    recorded,
    tomorrow,
    week_of,
)
from app.schema.settings import ReminderSettings, ReminderState, Weekday

SINGAPORE = ZoneInfo("Asia/Singapore")
FRIDAY = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
"""Friday 16 October 2026 at 18:00 in Singapore: the default reminder's slot."""
LAST_WEEK = ReminderState(last_slot=FRIDAY - timedelta(days=7))
"""The record once the previous Friday's reminder was dealt with."""


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """A moment in October 2026, in UTC."""
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def outcome_at(
    now: datetime,
    *,
    schedule: Schedule = DEFAULT_SCHEDULE,
    state: ReminderState = LAST_WEEK,
    last_journaled: datetime | None = None,
) -> Due | None:
    return due(schedule, state, now=now, last_journaled=last_journaled)


def test_the_reminder_waits_for_its_time() -> None:
    assert outcome_at(at(16, 9, 59)) is None


@pytest.mark.parametrize("now", [at(16, 10), at(16, 10, 59), at(16, 13, 59)])
def test_the_first_run_at_or_after_its_time_sends_it(now: datetime) -> None:
    """On Vercel the tick comes at any time in its hour, so that's 18:00 to 18:59 in
    Singapore."""
    assert outcome_at(now) == Due(FRIDAY, "send")


@pytest.mark.parametrize(
    "now",
    [
        pytest.param(at(16, 14), id="22:00 in Singapore, 4 hours late"),
        pytest.param(at(16, 19), id="03:00 on Saturday in Singapore, the old daily tick"),
        pytest.param(at(18, 10), id="two days late"),
    ],
)
def test_a_reminder_too_late_is_skipped_as_missed(now: datetime) -> None:
    assert outcome_at(now) == Due(FRIDAY, "missed")


def test_a_late_evening_reminder_is_missed_at_midnight() -> None:
    """Never after the local day ends, even within 4 hours."""
    late = Schedule(("fri",), time(21, 0), "Asia/Singapore")
    slot = datetime(2026, 10, 16, 21, 0, tzinfo=SINGAPORE)
    state = ReminderState(last_slot=slot - timedelta(days=7))
    before_midnight = datetime(2026, 10, 16, 23, 59, tzinfo=SINGAPORE)
    midnight = datetime(2026, 10, 17, 0, 0, tzinfo=SINGAPORE)
    assert outcome_at(before_midnight, schedule=late, state=state) == Due(slot, "send")
    assert outcome_at(midnight, schedule=late, state=state) == Due(slot, "missed")
    assert deadline(slot.astimezone(UTC), SINGAPORE) == midnight


def test_a_slot_is_dealt_with_once() -> None:
    state = recorded(LAST_WEEK, FRIDAY, message_id=7, sent=True)
    assert state == ReminderState(last_slot=FRIDAY, message_id=7)
    assert outcome_at(at(16, 10, 1), state=state) is None
    assert outcome_at(at(16, 13), state=state) is None


def test_only_the_latest_slot_counts() -> None:
    """With no record, or after weeks without a run, the older slots aren't sent in a burst."""
    assert outcome_at(at(16, 10), state=ReminderState()) == Due(FRIDAY, "send")
    assert outcome_at(at(16, 9), state=ReminderState()) == Due(FRIDAY - timedelta(days=7), "missed")


@pytest.mark.parametrize(
    ("ago", "outcome"),
    [
        (timedelta(days=2, hours=23, minutes=59), "journaled"),
        (timedelta(hours=1), "journaled"),
        (timedelta(days=3), "send"),
        (timedelta(days=30), "send"),
    ],
)
def test_no_reminder_after_journaling_in_the_last_3_days(ago: timedelta, outcome: Outcome) -> None:
    """FR-REM-2, as of when the reminder would go out."""
    now = at(16, 10, 30)
    assert outcome_at(now, last_journaled=now - ago) == Due(FRIDAY, outcome)


def test_no_reminder_while_paused() -> None:
    paused = Schedule(("fri",), time(18, 0), "Asia/Singapore", paused=True)
    assert outcome_at(at(16, 10), schedule=paused) == Due(FRIDAY, "paused")


def test_too_late_wins_over_the_other_reasons() -> None:
    paused = Schedule(("fri",), time(18, 0), "Asia/Singapore", paused=True)
    assert outcome_at(at(16, 14), schedule=paused, last_journaled=at(16, 9)) == Due(
        FRIDAY, "missed"
    )


def test_nothing_this_week_skips_the_rest_of_that_week_only() -> None:
    """FR-REM-3: the press is logged as the week's Monday, in the owner's time zone."""
    monday = date(2026, 10, 12)
    assert week_of(at(16, 10), SINGAPORE) == monday
    assert week_of(datetime(2026, 10, 18, 23, 59, tzinfo=SINGAPORE), SINGAPORE) == monday
    assert week_of(datetime(2026, 10, 19, 0, 0, tzinfo=SINGAPORE), SINGAPORE) == date(2026, 10, 19)
    state = LAST_WEEK.model_copy(update={"nothing_weeks": (monday,)})
    assert outcome_at(at(16, 10), state=state) == Due(FRIDAY, "nothing_this_week")
    next_week = recorded(state, FRIDAY)
    assert outcome_at(at(23, 10), state=next_week) == Due(at(23, 10), "send")


def test_remind_me_tomorrow_brings_it_back_the_next_evening() -> None:
    """Snoozed to the next local day at the schedule's time, which the daily tick lines up with."""
    sent = recorded(LAST_WEEK, FRIDAY, message_id=7, sent=True)
    saturday = tomorrow(DEFAULT_SCHEDULE, at(16, 10, 30))
    assert saturday == at(17, 10)
    assert tomorrow(DEFAULT_SCHEDULE, at(16, 16, 30)) == at(18, 10), "00:30 Saturday local"
    snoozed = sent.model_copy(update={"snoozed_until": saturday, "message_id": None})
    assert outcome_at(at(17, 9, 59), state=snoozed) is None
    assert outcome_at(at(17, 10, 30), state=snoozed) == Due(saturday, "send")
    assert outcome_at(at(17, 14), state=snoozed) == Due(saturday, "missed")
    done = recorded(snoozed, saturday, message_id=8, sent=True)
    assert done == ReminderState(last_slot=saturday, message_id=8)


def test_a_snooze_follows_the_same_rules() -> None:
    snoozed = recorded(LAST_WEEK, FRIDAY).model_copy(update={"snoozed_until": at(17, 10)})
    assert outcome_at(at(17, 10), state=snoozed, last_journaled=at(16, 12)) == Due(
        at(17, 10), "journaled"
    )


def test_a_later_slot_uses_up_an_earlier_snooze() -> None:
    every_day = Schedule(("fri", "sat"), time(18, 0), "Asia/Singapore")
    snoozed = recorded(LAST_WEEK, FRIDAY).model_copy(update={"snoozed_until": at(17, 10)})
    assert outcome_at(at(17, 10), schedule=every_day, state=snoozed) == Due(at(17, 10), "send")
    assert recorded(snoozed, at(17, 10)).snoozed_until is None
    assert recorded(snoozed, FRIDAY).snoozed_until == at(17, 10), "a later snooze is kept"


def test_a_message_that_couldnt_be_sent_still_takes_over_the_buttons() -> None:
    state = ReminderState(last_slot=FRIDAY - timedelta(days=7), message_id=6)
    assert recorded(state, FRIDAY, sent=True).message_id is None
    assert recorded(state, FRIDAY).message_id == 6, "a skipped slot leaves them"


def test_slots_follow_daylight_saving_time() -> None:
    """Europe/London leaves summer time on Sunday 25 October 2026: 18:00 there is 17:00 UTC on
    the Friday before, and 18:00 UTC on the Monday after."""
    london = Schedule(("mon", "fri"), time(18, 0), "Europe/London")
    assert latest_slot(london, at(26, 17, 59)) == at(23, 17)
    assert latest_slot(london, at(26, 18)) == at(26, 18)
    assert latest_slot(london, at(29, 12)) == at(26, 18)


def test_the_owners_setting_overrides_the_defaults_field_by_field() -> None:
    assert effective(ReminderSettings(), DEFAULT_SCHEDULE) == DEFAULT_SCHEDULE
    assert effective(ReminderSettings(paused=True), DEFAULT_SCHEDULE) == Schedule(
        ("fri",), time(18, 0), "Asia/Singapore", paused=True
    )
    stored = ReminderSettings(days=("mon", "thu"), time_zone="Europe/London")
    assert effective(stored, DEFAULT_SCHEDULE) == Schedule(
        ("mon", "thu"), time(18, 0), "Europe/London"
    )
    midnight = ReminderSettings(time=time(0, 0))
    assert effective(midnight, DEFAULT_SCHEDULE).time == time(0, 0)


@pytest.mark.parametrize(
    ("days", "words"),
    [
        (("fri",), "Fridays at 18:00 (Asia/Singapore)"),
        (("mon", "thu"), "Mondays and Thursdays at 18:00 (Asia/Singapore)"),
        (("mon", "wed", "fri"), "Mondays, Wednesdays and Fridays at 18:00 (Asia/Singapore)"),
        (("mon", "tue", "wed", "thu", "fri", "sat", "sun"), "every day at 18:00 (Asia/Singapore)"),
    ],
)
def test_a_schedule_in_words(days: tuple[Weekday, ...], words: str) -> None:
    schedule = Schedule(days, time(18, 0), "Asia/Singapore")
    assert describe(schedule) == words


def test_the_default_schedule_matches_the_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`DEFAULT_SCHEDULE` is what the settings give when nothing sets them (no `.env` here)."""
    monkeypatch.chdir(tmp_path)
    for name in ("REMINDER_DAYS", "REMINDER_TIME", "REMINDER_TIME_ZONE"):
        monkeypatch.delenv(name, raising=False)
    assert default_schedule(Settings()) == DEFAULT_SCHEDULE
