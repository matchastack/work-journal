"""When the owner's reminders go out (FR-REM-1, FR-REM-2, FR-REM-3, C6).

Pure functions, without I/O or a clock: `app/telegram/reminders.py` reads the owner's setting and
the bot's record, and the `send_reminders` job (`app/jobs.py`) sends what's due.

A schedule's *slots* are its days at its time, in its time zone. Each run of the job looks only at
the latest slot at or before now, and at a "Remind me tomorrow" snooze, so a pause, or a worker
stopped for a week, never brings a burst of old reminders. Each slot is dealt with once:

- The first run at or after its time sends it, if that run comes at most `LATE_BY` late and
  before the local day ends. On Vercel the tick comes once a day (C6), and a Friday 18:00 reminder
  mustn't arrive at 03:00 on Saturday.
- Otherwise it's skipped: too late, while paused, in a week marked "Nothing this week", or when
  the owner journaled in the last 3 days (FR-REM-2).

A skipped slot counts as dealt with, so a slot that passed while paused isn't sent after
`/resume`. Slots are compared and kept in UTC.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from typing import Literal
from zoneinfo import ZoneInfo

from app.config import Settings
from app.schema.settings import WEEKDAYS, ReminderSettings, ReminderState, Weekday

QUIET_FOR = timedelta(days=3)
"""No reminder goes out when the owner journaled this recently (FR-REM-2)."""
LATE_BY = timedelta(hours=4)
"""The latest a reminder may go out after its time, if the local day hasn't ended by then."""

Outcome = Literal["send", "journaled", "paused", "nothing_this_week", "missed"]
"""What becomes of a slot: it's sent, or skipped for one of the other reasons."""

_PLURALS: dict[Weekday, str] = {
    "mon": "Mondays",
    "tue": "Tuesdays",
    "wed": "Wednesdays",
    "thu": "Thursdays",
    "fri": "Fridays",
    "sat": "Saturdays",
    "sun": "Sundays",
}


@dataclass(frozen=True)
class Schedule:
    """When the owner's reminders go out: on `days`, at `time` in `time_zone`, unless paused."""

    days: tuple[Weekday, ...]
    time: clock_time
    time_zone: str
    paused: bool = False

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.time_zone)


DEFAULT_SCHEDULE = Schedule(("fri",), clock_time(18, 0), "Asia/Singapore")
"""The default schedule, Fridays at 18:00 in Singapore (FR-REM-1, OQ-4), as `REMINDER_DAYS`,
`REMINDER_TIME` and `REMINDER_TIME_ZONE` give it unless they're set."""


@dataclass(frozen=True)
class Due:
    """The latest slot not yet dealt with, and what becomes of it."""

    slot: datetime
    outcome: Outcome


def default_schedule(settings: Settings) -> Schedule:
    """The schedule of a user who hasn't chosen their own."""
    return Schedule(settings.reminder_days, settings.reminder_time, settings.reminder_time_zone)


def effective(stored: ReminderSettings, default: Schedule) -> Schedule:
    """The owner's schedule: what they set, and the default for the rest."""
    return Schedule(
        days=default.days if stored.days is None else stored.days,
        time=default.time if stored.time is None else stored.time,
        time_zone=default.time_zone if stored.time_zone is None else stored.time_zone,
        paused=stored.paused,
    )


def latest_slot(schedule: Schedule, now: datetime) -> datetime | None:
    """The schedule's latest slot at or before `now`, in UTC. None only without any days."""
    zone = schedule.zone
    today = now.astimezone(zone).date()
    for back in range(8):
        day = today - timedelta(days=back)
        if WEEKDAYS[day.weekday()] in schedule.days:
            slot = datetime.combine(day, schedule.time, tzinfo=zone).astimezone(UTC)
            if slot <= now:
                return slot
    return None


def deadline(slot: datetime, zone: ZoneInfo) -> datetime:
    """When a reminder for `slot` is too late to send: `LATE_BY` after it, or at the next local
    midnight if that comes first."""
    midnight = datetime.combine(
        slot.astimezone(zone).date() + timedelta(days=1), clock_time(0), tzinfo=zone
    )
    return min(slot + LATE_BY, midnight.astimezone(UTC))


def due(
    schedule: Schedule, state: ReminderState, *, now: datetime, last_journaled: datetime | None
) -> Due | None:
    """The slot to deal with at `now`, and whether to send it or why it's skipped: the later of
    the latest scheduled slot and a snooze, both at or before `now`. None when that slot was dealt
    with already. `last_journaled` is when the owner last sent the journal a message."""
    candidates = [
        slot
        for slot in (latest_slot(schedule, now), state.snoozed_until)
        if slot is not None and slot <= now
    ]
    if not candidates:
        return None
    slot = max(candidates)
    if state.last_slot is not None and slot <= state.last_slot:
        return None
    outcome: Outcome = "send"
    if now >= deadline(slot, schedule.zone):
        outcome = "missed"
    elif schedule.paused:
        outcome = "paused"
    elif week_of(slot, schedule.zone) in state.nothing_weeks:
        outcome = "nothing_this_week"
    elif last_journaled is not None and now - last_journaled < QUIET_FOR:
        outcome = "journaled"
    return Due(slot, outcome)


def recorded(
    state: ReminderState, slot: datetime, *, message_id: int | None = None, sent: bool = False
) -> ReminderState:
    """The record once `slot` is dealt with. A snooze up to it is used up. A reminder sent as
    `message_id`, or None when the bot couldn't send one, takes over the buttons from any older
    one."""
    snoozed = state.snoozed_until
    changes: dict[str, object] = {
        "last_slot": slot,
        "snoozed_until": snoozed if snoozed is not None and snoozed > slot else None,
    }
    if sent:
        changes["message_id"] = message_id
    return state.model_copy(update=changes)


def tomorrow(schedule: Schedule, now: datetime) -> datetime:
    """When "Remind me tomorrow", pressed at `now`, brings the reminder back: the next local day
    at the schedule's time, in UTC. On Vercel that's when the daily tick comes."""
    day = now.astimezone(schedule.zone).date() + timedelta(days=1)
    return datetime.combine(day, schedule.time, tzinfo=schedule.zone).astimezone(UTC)


def week_of(moment: datetime, zone: ZoneInfo) -> date:
    """The Monday of the week `moment` falls in, in `zone`."""
    day = moment.astimezone(zone).date()
    return day - timedelta(days=day.weekday())


def describe(schedule: Schedule) -> str:
    """The schedule in words, such as `Fridays at 18:00 (Asia/Singapore)`."""
    names = [_PLURALS[day] for day in schedule.days]
    if len(names) == len(WEEKDAYS):
        when = "every day"
    elif len(names) == 1:
        when = names[0]
    else:
        when = f"{', '.join(names[:-1])} and {names[-1]}"
    return f"{when} at {schedule.time:%H:%M} ({schedule.time_zone})"
