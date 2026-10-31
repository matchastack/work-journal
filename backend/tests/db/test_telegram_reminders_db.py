"""The owner's reminders in Telegram, against the test database (FR-REM-1, FR-REM-2, FR-REM-3):
the `send_reminders` job, the reminder's buttons, and `/pause` and `/resume`. Every run and press
is given its time, so the clock is frozen. When reminders are due is tested in
`test_reminders.py`.

The test database is shared by the whole session, and other tests leave linked chats in it, which
the job sends reminders to as well; each test looks only at its own chat."""

import json
import random
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx2
import pytest
from sqlalchemy import delete, func, select

import app.jobs as job_module
from app.config import Settings, get_settings
from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import JournalMessage, TelegramLink, User
from app.db.settings import (
    get_reminder_settings,
    get_reminder_state,
    save_reminder_settings,
    save_reminder_state,
)
from app.jobs import database, send_reminders
from app.reminders import DEFAULT_SCHEDULE, Schedule
from app.schema.settings import ReminderSettings, ReminderState
from app.telegram.api import BotCall, send
from app.telegram.journal import Handled, handle_update
from app.telegram.reminders import (
    ALREADY_PAUSED,
    KEYBOARD,
    NOTED,
    NOTHING_THIS_WEEK,
    PASSED,
    PAUSED,
    PAUSED_SNOOZE,
    REMIND_TOMORROW,
    REMINDER,
)

pytestmark = [pytest.mark.anyio, pytest.mark.reminders]

FRIDAY = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
"""Friday 16 October 2026 at 18:00 in Singapore, when the default reminder is due."""
SATURDAY = FRIDAY + timedelta(days=1)
PRESSED = FRIDAY + timedelta(minutes=30)
"""When the owner presses a button under Friday's reminder."""
FIRST_ID = 701
"""The ID of the first message the bot sends to a chat (`Telegram`)."""
QUERY_ID = "4382bfdwdsb323b2d9"
NOTE = "Paired with the new hire on the billing service."
_random = random.SystemRandom()


@pytest.fixture(autouse=True)
def key_ring() -> Iterator[None]:
    use_key_ring(KeyRing.parse(new_key("k1")))
    yield
    use_key_ring(None)


@pytest.fixture
def chat_id() -> int:
    return _random.randrange(10**9, 10**12)


@pytest.fixture
def job_settings(
    database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    """Jobs read their settings as the worker does, with the default schedule: from an empty
    directory, so that no `backend/.env` adds a bot token or another schedule."""
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test-token")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class Telegram:
    """A stand-in for the Bot API: it keeps each message sent, numbered per chat from
    `FIRST_ID`, and refuses those to the chats in `failing`."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.failing: set[int] = set()

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        assert request.url.path.endswith("/sendMessage")
        message = json.loads(request.content)
        if message["chat_id"] in self.failing:
            return httpx2.Response(502, text="Bad Gateway")
        self.sent.append(message)
        message_id = FIRST_ID + len(self.to(message["chat_id"])) - 1
        return httpx2.Response(200, json={"ok": True, "result": {"message_id": message_id}})

    def to(self, chat_id: int) -> list[dict[str, Any]]:
        return [message for message in self.sent if message["chat_id"] == chat_id]


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> Telegram:
    bot = Telegram()
    transport = httpx2.MockTransport(bot.handle)
    monkeypatch.setattr(
        job_module, "_telegram_http", lambda: httpx2.AsyncClient(transport=transport)
    )
    return bot


Owner = Callable[..., Awaitable[uuid.UUID]]


@pytest.fixture
async def owner(job_settings: None, chat_id: int) -> AsyncIterator[Owner]:
    """Make a user linked to `chat_id`, or to `chat`, committed, as jobs see it: with a journal
    message sent at `journaled`, and a reminder setting and record, if given. The users made are
    deleted afterwards, with all their rows."""
    users: list[uuid.UUID] = []

    async def make(
        *,
        chat: int = chat_id,
        journaled: datetime | None = None,
        reminders: ReminderSettings | None = None,
        state: ReminderState | None = None,
    ) -> uuid.UUID:
        async with database() as session:
            user = User()
            session.add(user)
            await session.flush()
            session.add(TelegramLink(user_id=user.id, chat_id=chat))
            if journaled is not None:
                session.add(
                    JournalMessage(
                        user_id=user.id,
                        chat_id=chat_id,
                        message_id=1,
                        sender="owner",
                        text=NOTE,
                        sent_at=journaled,
                    )
                )
            if reminders is not None:
                await save_reminder_settings(session, user.id, reminders)
            if state is not None:
                await save_reminder_state(session, user.id, state)
        users.append(user.id)
        return user.id

    yield make
    async with database() as session:
        await session.execute(delete(User).where(User.id.in_(users)))


async def sweep(at: datetime, *, runs_at: datetime | None = None) -> None:
    """Run the job the scheduler defers for `at`, as it starts then, or at `runs_at` once it
    waited in the queue."""
    with patch.object(job_module, "_clock", lambda: runs_at or at):
        await send_reminders(timestamp=int(at.timestamp()))


async def state_of(user_id: uuid.UUID) -> ReminderState:
    async with database() as session:
        return await get_reminder_state(session, user_id)


async def setting_of(user_id: uuid.UUID) -> ReminderSettings:
    async with database() as session:
        return await get_reminder_settings(session, user_id)


async def handle(
    payload: str, *, now: datetime = PRESSED, reminders: Schedule = DEFAULT_SCHEDULE
) -> Handled:
    """Handle an update as the webhook does, committed."""
    async with database() as session:
        return await handle_update(session, payload, now=now, reminders=reminders)


def press(chat_id: int, data: str, *, message_id: int = FIRST_ID) -> str:
    """An update for a press of the button carrying `data`, under message `message_id`."""
    return json.dumps(
        {
            "update_id": _random.randrange(10**9, 10**12),
            "callback_query": {
                "id": QUERY_ID,
                "from": {"id": chat_id, "is_bot": False, "first_name": "Casey"},
                "chat_instance": "-1234",
                "message": {
                    "message_id": message_id,
                    "from": {"id": 99, "is_bot": True, "first_name": "Journal"},
                    "chat": {"id": chat_id, "type": "private"},
                    "date": int(FRIDAY.timestamp()),
                    "text": REMINDER,
                },
                "data": data,
            },
        }
    )


def command(chat_id: int, text: str) -> str:
    return json.dumps(
        {
            "update_id": _random.randrange(10**9, 10**12),
            "message": {
                "message_id": _random.randrange(1, 10**6),
                "from": {"id": chat_id, "is_bot": False, "first_name": "Casey"},
                "chat": {"id": chat_id, "type": "private"},
                "date": int(PRESSED.timestamp()),
                "text": text,
            },
        }
    )


def answered(notice: str | None = None) -> BotCall:
    params: dict[str, Any] = {"callback_query_id": QUERY_ID}
    if notice is not None:
        params["text"] = notice
    return BotCall("answerCallbackQuery", params)


def without_buttons(chat_id: int, message_id: int = FIRST_ID) -> BotCall:
    return BotCall("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id})


async def journaled(chat_id: int) -> int:
    async with database() as session:
        count = select(func.count()).where(JournalMessage.chat_id == chat_id)
        return await session.scalar(count) or 0


def reminder(chat_id: int) -> dict[str, Any]:
    """The reminder, as the bot sends it."""
    return {"chat_id": chat_id, "text": REMINDER, "reply_markup": KEYBOARD}


def sent(slot: datetime, message_id: int = FIRST_ID) -> ReminderState:
    """The record once the reminder for `slot` went out as `message_id`."""
    return ReminderState(last_slot=slot, message_id=message_id, message_slot=slot)


async def test_the_reminder_goes_out_once_with_its_buttons(
    owner: Owner, chat_id: int, telegram: Telegram
) -> None:
    user_id = await owner()
    await sweep(FRIDAY - timedelta(minutes=1))
    assert telegram.to(chat_id) == []
    await sweep(FRIDAY)
    assert telegram.to(chat_id) == [reminder(chat_id)]
    assert await state_of(user_id) == sent(FRIDAY)
    await sweep(FRIDAY + timedelta(minutes=1))
    await sweep(FRIDAY + timedelta(hours=2))
    assert telegram.to(chat_id) == [reminder(chat_id)], "once"


async def test_no_reminder_after_journaling_in_the_last_3_days(
    owner: Owner, chat_id: int, telegram: Telegram
) -> None:
    """FR-REM-2. The reminder is dealt with, so it doesn't go out later that evening either."""
    user_id = await owner(journaled=FRIDAY - timedelta(days=2))
    await sweep(FRIDAY)
    await sweep(FRIDAY + timedelta(hours=1))
    assert telegram.to(chat_id) == []
    assert await state_of(user_id) == ReminderState(last_slot=FRIDAY)


async def test_a_reminder_goes_out_3_days_after_journaling(
    owner: Owner, chat_id: int, telegram: Telegram
) -> None:
    await owner(journaled=FRIDAY - timedelta(days=3))
    await sweep(FRIDAY)
    assert telegram.to(chat_id) == [reminder(chat_id)]


async def test_no_reminder_while_paused(owner: Owner, chat_id: int, telegram: Telegram) -> None:
    user_id = await owner(reminders=ReminderSettings(paused=True))
    await sweep(FRIDAY)
    assert telegram.to(chat_id) == []
    assert await state_of(user_id) == ReminderState(last_slot=FRIDAY)


async def test_each_owner_has_their_own_schedule_and_time_zone(
    owner: Owner, chat_id: int, telegram: Telegram
) -> None:
    """Fridays at 07:30 in London, which is 06:30 UTC in October; not 18:00 in Singapore."""
    early = ReminderSettings(time=time(7, 30), time_zone="Europe/London")
    user_id = await owner(reminders=early)
    await sweep(datetime(2026, 10, 16, 6, 29, tzinfo=UTC))
    assert telegram.to(chat_id) == []
    await sweep(datetime(2026, 10, 16, 6, 30, tzinfo=UTC))
    await sweep(FRIDAY)
    assert telegram.to(chat_id) == [reminder(chat_id)]
    assert await state_of(user_id) == sent(datetime(2026, 10, 16, 6, 30, tzinfo=UTC))


async def test_a_reminder_telegram_refuses_is_tried_again_while_its_on_time(
    owner: Owner, chat_id: int, telegram: Telegram
) -> None:
    """The job doesn't fail, and the owner's record is undone, so the next run tries again."""
    user_id = await owner()
    telegram.failing.add(chat_id)
    await sweep(FRIDAY)
    assert telegram.to(chat_id) == []
    assert await state_of(user_id) == ReminderState()
    telegram.failing.clear()
    await sweep(FRIDAY + timedelta(minutes=5))
    assert telegram.to(chat_id) == [reminder(chat_id)]
    assert await state_of(user_id) == sent(FRIDAY)


@pytest.mark.parametrize("failing", [0, 1], ids=["the first owner's", "the second owner's"])
async def test_one_owners_failure_leaves_the_other_owners_reminders_alone(
    owner: Owner, chat_id: int, telegram: Telegram, failing: int
) -> None:
    """Each owner has a transaction of their own, and the job goes on after a failure. Either
    owner's chat fails, whichever the job comes to first."""
    chats = [chat_id, chat_id + 1]
    users = [await owner(chat=chat) for chat in chats]
    telegram.failing.add(chats[failing])
    await sweep(FRIDAY)
    other = 1 - failing
    assert telegram.to(chats[other]) == [reminder(chats[other])]
    assert await state_of(users[other]) == sent(FRIDAY)
    assert telegram.to(chats[failing]) == []
    assert await state_of(users[failing]) == ReminderState()


async def test_a_reminder_the_bot_cant_send_still_counts_as_sent(
    owner: Owner, chat_id: int, telegram: Telegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without TELEGRAM_BOT_TOKEN, nothing is sent, and no reminder's buttons work."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    get_settings.cache_clear()
    user_id = await owner(state=ReminderState(message_id=FIRST_ID))
    await sweep(FRIDAY)
    assert telegram.to(chat_id) == []
    assert await state_of(user_id) == ReminderState(last_slot=FRIDAY)


async def test_a_reminder_too_late_is_skipped(
    owner: Owner, chat_id: int, telegram: Telegram
) -> None:
    """At 03:00 on Saturday in Singapore, the old time of the daily tick (C6)."""
    user_id = await owner()
    await sweep(FRIDAY + timedelta(hours=9))
    assert telegram.to(chat_id) == []
    assert await state_of(user_id) == ReminderState(last_slot=FRIDAY)


@pytest.mark.parametrize(
    "runs_at",
    [
        pytest.param(FRIDAY + timedelta(hours=4, minutes=30), id="at 22:30, over 4 hours late"),
        pytest.param(SATURDAY + timedelta(minutes=5), id="at the next day's tick"),
    ],
)
async def test_a_job_that_waited_in_the_queue_decides_as_of_when_it_runs(
    owner: Owner, chat_id: int, telegram: Telegram, runs_at: datetime
) -> None:
    """A tick runs jobs for a short time only, so the job deferred for 18:05 on Friday can wait
    behind slower ones, until a later tick (C6). By then the reminder is too late."""
    user_id = await owner()
    await sweep(FRIDAY + timedelta(minutes=5), runs_at=runs_at)
    assert telegram.to(chat_id) == []
    assert await state_of(user_id) == ReminderState(last_slot=FRIDAY)


async def test_a_job_that_waited_less_still_sends_the_reminder_on_time(
    owner: Owner, chat_id: int, telegram: Telegram
) -> None:
    await owner()
    await sweep(FRIDAY - timedelta(minutes=1), runs_at=FRIDAY + timedelta(minutes=3))
    assert telegram.to(chat_id) == [reminder(chat_id)]


# --- The reminder's buttons (FR-REM-3) -----------------------------------------------------------


async def test_nothing_this_week_is_logged_and_skips_the_rest_of_the_week(
    owner: Owner, chat_id: int, telegram: Telegram
) -> None:
    """Logged as the week's Monday in the owner's time zone. With reminders on Fridays and
    Saturdays, Saturday's is skipped, and the next Friday's goes out."""
    user_id = await owner(reminders=ReminderSettings(days=("fri", "sat")))
    await sweep(FRIDAY)
    handled = await handle(press(chat_id, NOTHING_THIS_WEEK))
    assert handled == Handled((answered(), without_buttons(chat_id), send(chat_id, NOTED)))
    monday = date(2026, 10, 12)
    assert await state_of(user_id) == ReminderState(last_slot=FRIDAY, nothing_weeks=(monday,))

    await sweep(SATURDAY)
    assert telegram.to(chat_id) == [reminder(chat_id)]
    next_friday = FRIDAY + timedelta(days=7)
    await sweep(next_friday)
    assert telegram.to(chat_id) == [reminder(chat_id)] * 2
    assert await state_of(user_id) == sent(next_friday, FIRST_ID + 1).model_copy(
        update={"nothing_weeks": (monday,)}
    )


async def test_nothing_this_week_pressed_after_the_weekend_means_the_reminders_week(
    owner: Owner, chat_id: int, telegram: Telegram
) -> None:
    """Pressed under Friday's reminder at 09:00 on Monday in Singapore, it logs the week that
    just ended, and the next Friday's reminder still goes out."""
    user_id = await owner()
    await sweep(FRIDAY)
    monday = datetime(2026, 10, 19, 1, 0, tzinfo=UTC)
    handled = await handle(press(chat_id, NOTHING_THIS_WEEK), now=monday)
    assert handled == Handled((answered(), without_buttons(chat_id), send(chat_id, NOTED)))
    noted = ReminderState(last_slot=FRIDAY, nothing_weeks=(date(2026, 10, 12),))
    assert await state_of(user_id) == noted

    await sweep(FRIDAY + timedelta(days=7))
    assert telegram.to(chat_id) == [reminder(chat_id)] * 2


async def test_remind_me_tomorrow_brings_the_reminder_back_the_next_evening(
    owner: Owner, chat_id: int, telegram: Telegram
) -> None:
    """At the schedule's time, when the daily tick comes on Vercel."""
    user_id = await owner()
    await sweep(FRIDAY)
    handled = await handle(press(chat_id, REMIND_TOMORROW))
    snoozed = send(chat_id, "OK, I'll remind you tomorrow at 18:00.")
    assert handled == Handled((answered(), without_buttons(chat_id), snoozed))
    assert await state_of(user_id) == ReminderState(last_slot=FRIDAY, snoozed_until=SATURDAY)

    await sweep(SATURDAY - timedelta(minutes=1))
    assert telegram.to(chat_id) == [reminder(chat_id)]
    await sweep(SATURDAY)
    assert telegram.to(chat_id) == [reminder(chat_id)] * 2
    assert await state_of(user_id) == sent(SATURDAY, FIRST_ID + 1)


async def test_remind_me_tomorrow_while_paused_only_says_so(
    owner: Owner, chat_id: int, telegram: Telegram
) -> None:
    """The pause would skip that reminder, so the press changes nothing, and once reminders are
    back on, the button works."""
    user_id = await owner(state=sent(FRIDAY))
    await handle(command(chat_id, "/pause"))
    handled = await handle(press(chat_id, REMIND_TOMORROW))
    assert handled == Handled((answered(PAUSED_SNOOZE),))
    assert await state_of(user_id) == sent(FRIDAY)

    await handle(command(chat_id, "/resume"))
    handled = await handle(press(chat_id, REMIND_TOMORROW))
    snoozed = send(chat_id, "OK, I'll remind you tomorrow at 18:00.")
    assert handled == Handled((answered(), without_buttons(chat_id), snoozed))
    await sweep(SATURDAY)
    assert telegram.to(chat_id) == [reminder(chat_id)]


@pytest.mark.parametrize(
    ("message_id", "newest"),
    [
        pytest.param(FIRST_ID, FIRST_ID + 1, id="an older reminder"),
        pytest.param(FIRST_ID, None, id="a reminder already answered"),
    ],
)
@pytest.mark.parametrize("data", [NOTHING_THIS_WEEK, REMIND_TOMORROW])
async def test_only_the_newest_reminders_buttons_work(
    owner: Owner, chat_id: int, message_id: int, newest: int | None, data: str
) -> None:
    state = ReminderState(last_slot=FRIDAY, message_id=newest)
    user_id = await owner(state=state)
    handled = await handle(press(chat_id, data, message_id=message_id))
    assert handled == Handled((answered(PASSED),))
    assert await state_of(user_id) == state


# --- /pause and /resume (FR-REM-3, FR-SET-2) -----------------------------------------------------


async def test_pause_stops_reminders_and_resume_restarts_them(
    owner: Owner, chat_id: int, telegram: Telegram
) -> None:
    """Neither command is journaled. A reminder whose time passed while paused isn't sent late,
    and the pause leaves the schedule following the defaults."""
    user_id = await owner()
    assert await handle(command(chat_id, "/pause")) == Handled((send(chat_id, PAUSED),))
    assert await setting_of(user_id) == ReminderSettings(paused=True)
    assert await handle(command(chat_id, "/pause")) == Handled((send(chat_id, ALREADY_PAUSED),))
    await sweep(FRIDAY)
    assert telegram.to(chat_id) == []

    resumed = "Reminders are back on: Fridays at 18:00 (Asia/Singapore)."
    assert await handle(command(chat_id, "/resume")) == Handled((send(chat_id, resumed),))
    assert await setting_of(user_id) == ReminderSettings()
    already = "Reminders are already on: Fridays at 18:00 (Asia/Singapore)."
    assert await handle(command(chat_id, "/RESUME")) == Handled((send(chat_id, already),))
    await sweep(FRIDAY + timedelta(minutes=30))
    assert telegram.to(chat_id) == []
    await sweep(FRIDAY + timedelta(days=7))
    assert telegram.to(chat_id) == [reminder(chat_id)]
    assert await journaled(chat_id) == 0


async def test_resume_says_when_the_owners_reminders_go_out(owner: Owner, chat_id: int) -> None:
    """The owner's own choices, and the defaults for the rest."""
    await owner(reminders=ReminderSettings(time=time(7, 30), paused=True))
    defaults = Schedule(("mon", "thu"), time(18, 0), "Europe/London")
    handled = await handle(command(chat_id, "/resume"), reminders=defaults)
    resumed = "Reminders are back on: Mondays and Thursdays at 07:30 (Europe/London)."
    assert handled == Handled((send(chat_id, resumed),))


async def test_help_lists_pause_and_resume(owner: Owner, chat_id: int) -> None:
    """FR-CAP-7, and Telegram's command menu."""
    await owner()
    [call] = (await handle(command(chat_id, "/help"))).calls
    listed = [line.split(" - ")[0] for line in call.params["text"].splitlines() if line[:1] == "/"]
    assert listed == ["/done", "/skip", "/refresh", "/pause", "/resume", "/help"]
    assert "/pause - stop reminders\n/resume - restart reminders" in call.params["text"]
