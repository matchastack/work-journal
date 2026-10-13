"""Answering the follow-up question about a closed entry in Telegram (FR-CAP-6): the owner's next
message, the Skip button and `/skip`. Asking is tested in `test_entry_processing_db.py`."""

import json
import random
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import JournalEntry, JournalMessage, TelegramLink, User
from app.telegram.api import BotCall
from app.telegram.follow_up import NOT_WAITING, NOTHING_TO_SKIP, SKIP, SKIPPED
from app.telegram.journal import Handled, handle_update, help_text

pytestmark = pytest.mark.anyio

QUESTION_ID = 910
"""The bot's follow-up question, as a message in the owner's chat."""
SENT = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
"""When the owner's messages in these tests are sent."""
ANSWER = "it went from ~50 min to 12"
_random = random.SystemRandom()


@pytest.fixture(autouse=True)
def key_ring() -> Iterator[None]:
    use_key_ring(KeyRing.parse(new_key("k1")))
    yield
    use_key_ring(None)


@pytest.fixture
def chat_id() -> int:
    return _random.randrange(10**9, 10**12)


async def asked_entry(
    session: AsyncSession, chat_id: int, *, asked: datetime = SENT - timedelta(minutes=5)
) -> tuple[uuid.UUID, uuid.UUID]:
    """A user linked to `chat_id`, with one closed entry whose follow-up question, asked at
    `asked`, waits for an answer. Returns both IDs."""
    user = User()
    session.add(user)
    await session.flush()
    session.add(TelegramLink(user_id=user.id, chat_id=chat_id))
    entry = JournalEntry(
        user_id=user.id,
        opened_at=asked - timedelta(minutes=10),
        last_message_at=asked - timedelta(minutes=1),
        closed_at=asked - timedelta(minutes=1),
        closed_by="done",
        follow_up_at=asked,
        follow_up_question_id=QUESTION_ID,
        awaiting_follow_up=True,
    )
    session.add(entry)
    await session.flush()
    return user.id, entry.id


def message(chat_id: int, text: str, message_id: int = 60) -> str:
    return json.dumps(
        {
            "update_id": _random.randrange(10**9, 10**12),
            "message": {
                "message_id": message_id,
                "from": {"id": chat_id, "is_bot": False, "first_name": "Casey"},
                "chat": {"id": chat_id, "type": "private"},
                "date": int(SENT.timestamp()),
                "text": text,
            },
        }
    )


def press(chat_id: int, message_id: int = QUESTION_ID) -> str:
    """An update for a press of the Skip button under message `message_id`."""
    return json.dumps(
        {
            "update_id": _random.randrange(10**9, 10**12),
            "callback_query": {
                "id": "5493cgexetc434c3e0",
                "from": {"id": chat_id, "is_bot": False, "first_name": "Casey"},
                "chat_instance": "-1234",
                "message": {
                    "message_id": message_id,
                    "from": {"id": 99, "is_bot": True, "first_name": "Journal"},
                    "chat": {"id": chat_id, "type": "private"},
                    "date": int(SENT.timestamp()) - 300,
                    "text": "How much faster did the nightly export get?",
                },
                "data": SKIP,
            },
        }
    )


def answered_press(notice: str | None = None) -> BotCall:
    params: dict[str, Any] = {"callback_query_id": "5493cgexetc434c3e0"}
    if notice is not None:
        params["text"] = notice
    return BotCall("answerCallbackQuery", params)


def without_skip(chat_id: int) -> BotCall:
    """The question, edited to drop its Skip button."""
    return BotCall("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": QUESTION_ID})


def sent(chat_id: int, text: str) -> BotCall:
    return BotCall("sendMessage", {"chat_id": chat_id, "text": text})


async def awaits(session: AsyncSession, entry_id: uuid.UUID) -> bool:
    entry = await session.get(JournalEntry, entry_id, populate_existing=True)
    assert entry is not None
    return entry.awaiting_follow_up


async def entries_of(session: AsyncSession, chat_id: int) -> list[tuple[uuid.UUID | None, str]]:
    """Each journaled message's entry and text, in the order the owner sent them."""
    rows = await session.execute(
        select(JournalMessage.entry_id, JournalMessage.text)
        .where(JournalMessage.chat_id == chat_id)
        .order_by(JournalMessage.message_id)
    )
    return [(entry_id, text) for entry_id, text in rows]


async def test_the_next_message_answers_and_joins_the_entry(
    session: AsyncSession, chat_id: int
) -> None:
    """The question loses its Skip button, and the entry's jobs go on to its role."""
    _, entry_id = await asked_entry(session, chat_id)
    handled = await handle_update(session, message(chat_id, ANSWER))
    assert handled == Handled((without_skip(chat_id),), ready=entry_id)
    assert await entries_of(session, chat_id) == [(entry_id, ANSWER)]
    assert await awaits(session, entry_id) is False

    await handle_update(session, message(chat_id, "Shipped the export fix.", message_id=61))
    [_, (later, _)] = await entries_of(session, chat_id)
    assert later != entry_id, "only one message answers"


async def test_a_message_after_the_timeout_is_a_new_note(
    session: AsyncSession, chat_id: int
) -> None:
    """The question has lapsed: the job lets the entry go on to its role."""
    _, entry_id = await asked_entry(session, chat_id, asked=SENT - timedelta(minutes=30))
    handled = await handle_update(session, message(chat_id, "Shipped the export fix."))
    assert handled == Handled()
    [(new_entry, _)] = await entries_of(session, chat_id)
    assert new_entry not in (None, entry_id)


async def test_the_skip_button_skips_the_question(session: AsyncSession, chat_id: int) -> None:
    _, entry_id = await asked_entry(session, chat_id)
    handled = await handle_update(session, press(chat_id))
    assert handled == Handled((answered_press(), without_skip(chat_id)), ready=entry_id)
    assert await awaits(session, entry_id) is False

    await handle_update(session, message(chat_id, "Shipped the export fix."))
    [(new_entry, _)] = await entries_of(session, chat_id)
    assert new_entry != entry_id, "the next message is a new note"


@pytest.mark.parametrize("asked_ago", [5, 45], ids=["waiting", "lapsed"])
async def test_skip_skips_the_waiting_question(
    session: AsyncSession, chat_id: int, asked_ago: int
) -> None:
    """Even one that has lapsed but hasn't been let go yet."""
    _, entry_id = await asked_entry(session, chat_id, asked=SENT - timedelta(minutes=asked_ago))
    handled = await handle_update(session, message(chat_id, "/skip"))
    assert handled == Handled((sent(chat_id, SKIPPED), without_skip(chat_id)), ready=entry_id)
    assert await awaits(session, entry_id) is False
    assert await entries_of(session, chat_id) == [], "commands aren't journaled"


async def test_skip_with_no_question_says_so(session: AsyncSession, chat_id: int) -> None:
    _, entry_id = await asked_entry(session, chat_id)
    await handle_update(session, press(chat_id))
    handled = await handle_update(session, message(chat_id, "/skip"))
    assert handled == Handled((sent(chat_id, NOTHING_TO_SKIP),))
    assert await awaits(session, entry_id) is False


@pytest.mark.parametrize("message_id", [QUESTION_ID, QUESTION_ID - 1], ids=["skipped", "another"])
async def test_a_skip_button_that_no_longer_applies_changes_nothing(
    session: AsyncSession, chat_id: int, message_id: int
) -> None:
    _, entry_id = await asked_entry(session, chat_id)
    if message_id == QUESTION_ID:
        await handle_update(session, press(chat_id))
    handled = await handle_update(session, press(chat_id, message_id))
    assert handled == Handled((answered_press(NOT_WAITING),))
    assert await awaits(session, entry_id) is (message_id != QUESTION_ID)


async def test_commands_still_work_while_an_answer_is_awaited(
    session: AsyncSession, chat_id: int
) -> None:
    _, entry_id = await asked_entry(session, chat_id)
    handled = await handle_update(session, message(chat_id, "/help"))
    assert handled == Handled((sent(chat_id, help_text(timedelta(minutes=30))),))
    assert "/skip - skip the question about the last entry" in help_text(timedelta(minutes=30))
    assert await awaits(session, entry_id) is True


async def test_a_new_roles_name_comes_before_the_answer(
    session: AsyncSession, chat_id: int
) -> None:
    """The owner pressed New role under another entry's question after the follow-up question
    came: their next message names the role, as the bot just asked."""
    user_id, entry_id = await asked_entry(session, chat_id)
    other = JournalEntry(
        user_id=user_id,
        opened_at=SENT - timedelta(hours=2),
        last_message_at=SENT - timedelta(hours=2),
        closed_at=SENT - timedelta(hours=1),
        closed_by="quiet",
        role_asked_at=SENT - timedelta(hours=1),
        naming_role=True,
    )
    session.add(other)
    await session.flush()
    handled = await handle_update(session, message(chat_id, "Data Engineer, Fabrikam"))
    assert handled.ready == other.id
    assert await entries_of(session, chat_id) == []
    assert await awaits(session, entry_id) is True
