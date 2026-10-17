"""The owner's reminders in Telegram, against the test database (FR-REM-1, FR-REM-2): the
`send_reminders` job. Every run is given its time, so the clock is frozen. When reminders are due
is tested in `test_reminders.py`.

The test database is shared by the whole session, and other tests leave linked chats in it, which
the job sends reminders to as well; each test looks only at its own chat."""

import json
import random
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime, time, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import delete

import app.jobs as job_module
from app.config import get_settings
from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import JournalMessage, TelegramLink, User
from app.db.settings import get_reminder_state, save_reminder_settings, save_reminder_state
from app.jobs import database, send_reminders
from app.schema.settings import ReminderSettings, ReminderState
from app.telegram.reminders import KEYBOARD, REMINDER

pytestmark = pytest.mark.anyio

FRIDAY = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
"""Friday 16 October 2026 at 18:00 in Singapore, when the default reminder is due."""
FIRST_ID = 701
"""The ID of the first message the bot sends to a chat (`Telegram`)."""
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
def job_settings(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Jobs read their settings as the worker does, with the default schedule."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test-token")
    for name in ("REMINDER_DAYS", "REMINDER_TIME", "REMINDER_TIME_ZONE"):
        monkeypatch.delenv(name, raising=False)
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
    """Make a user linked to `chat_id`, committed, as jobs see it: with a journal message sent
    at `journaled`, and a reminder setting and record, if given. The users made are deleted
    afterwards, with all their rows."""
    users: list[uuid.UUID] = []

    async def make(
        *,
        journaled: datetime | None = None,
        reminders: ReminderSettings | None = None,
        state: ReminderState | None = None,
    ) -> uuid.UUID:
        async with database() as session:
            user = User()
            session.add(user)
            await session.flush()
            session.add(TelegramLink(user_id=user.id, chat_id=chat_id))
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


async def sweep(at: datetime) -> None:
    """Run the job as the scheduler does for `at`."""
    await send_reminders(timestamp=int(at.timestamp()))


async def state_of(user_id: uuid.UUID) -> ReminderState:
    async with database() as session:
        return await get_reminder_state(session, user_id)


def reminder(chat_id: int) -> dict[str, Any]:
    """The reminder, as the bot sends it."""
    return {"chat_id": chat_id, "text": REMINDER, "reply_markup": KEYBOARD}


async def test_the_reminder_goes_out_once_with_its_buttons(
    owner: Owner, chat_id: int, telegram: Telegram
) -> None:
    user_id = await owner()
    await sweep(FRIDAY - timedelta(minutes=1))
    assert telegram.to(chat_id) == []
    await sweep(FRIDAY)
    assert telegram.to(chat_id) == [reminder(chat_id)]
    assert await state_of(user_id) == ReminderState(last_slot=FRIDAY, message_id=FIRST_ID)
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
    slot = datetime(2026, 10, 16, 6, 30, tzinfo=UTC)
    assert await state_of(user_id) == ReminderState(last_slot=slot, message_id=FIRST_ID)


async def test_a_reminder_telegram_refuses_is_tried_again_while_its_on_time(
    owner: Owner, chat_id: int, telegram: Telegram
) -> None:
    """Only this owner's record is undone, and the job doesn't fail, so other owners still get
    theirs."""
    user_id = await owner()
    telegram.failing.add(chat_id)
    await sweep(FRIDAY)
    assert telegram.to(chat_id) == []
    assert await state_of(user_id) == ReminderState()
    telegram.failing.clear()
    await sweep(FRIDAY + timedelta(minutes=5))
    assert telegram.to(chat_id) == [reminder(chat_id)]
    assert await state_of(user_id) == ReminderState(last_slot=FRIDAY, message_id=FIRST_ID)


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
