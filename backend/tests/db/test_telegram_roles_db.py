"""Choosing which role a journal entry is about, in Telegram (FR-CAP-11): the question's buttons,
and the message that names a new role. Asking is tested in `test_entry_processing_db.py`."""

import json
import random
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import JournalEntry, JournalMessage, TelegramLink, User
from app.db.profiles import save_version
from app.schema.profile import Profile
from app.telegram.api import BotCall
from app.telegram.journal import Handled, handle_update, help_text
from app.telegram.roles import (
    ANSWERED,
    GONE,
    NAME_LIMIT,
    NAME_PROMPT,
    NAME_TOO_LONG,
    NEW_ROLE,
    NOT_IN_PROFILE,
)

pytestmark = pytest.mark.anyio

PROFILE = Profile.model_validate_json(
    (Path(__file__).parents[1] / "fixtures" / "profile.json").read_text(encoding="utf-8")
)
QUESTION_ID = 900
"""The bot's question about the entry, as a message in the owner's chat."""
CLOSED = datetime(2026, 10, 4, 9, 30, tzinfo=UTC)
NEW_NAME = "Data Engineer, Fabrikam"
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
    session: AsyncSession, chat_id: int, *, profile: Profile | None = PROFILE, **state: Any
) -> tuple[uuid.UUID, uuid.UUID]:
    """A user linked to `chat_id`, with `profile` loaded and one closed entry the bot has asked
    about. Returns both IDs."""
    user = User()
    session.add(user)
    await session.flush()
    session.add(TelegramLink(user_id=user.id, chat_id=chat_id))
    if profile is not None:
        await save_version(session, user.id, profile, author="owner")
    state.setdefault("role_question_id", QUESTION_ID)
    entry = JournalEntry(
        user_id=user.id,
        opened_at=CLOSED - timedelta(minutes=5),
        last_message_at=CLOSED,
        closed_at=CLOSED,
        closed_by="done",
        role_asked_at=CLOSED,
        **state,
    )
    session.add(entry)
    await session.flush()
    return user.id, entry.id


def press(chat_id: int, data: str, *, message_id: int = QUESTION_ID) -> str:
    """An update for a press of the button carrying `data`, under message `message_id`."""
    return json.dumps(
        {
            "update_id": _random.randrange(10**9, 10**12),
            "callback_query": {
                "id": "4382bfdwdsb323b2d9",
                "from": {"id": chat_id, "is_bot": False, "first_name": "Casey"},
                "chat_instance": "-1234",
                "message": {
                    "message_id": message_id,
                    "from": {"id": 99, "is_bot": True, "first_name": "Journal"},
                    "chat": {"id": chat_id, "type": "private"},
                    "date": 1791106200,
                    "text": "Which role is this entry about?",
                },
                "data": data,
            },
        }
    )


def message(chat_id: int, text: str, message_id: int = 50) -> str:
    return json.dumps(
        {
            "update_id": _random.randrange(10**9, 10**12),
            "message": {
                "message_id": message_id,
                "from": {"id": chat_id, "is_bot": False, "first_name": "Casey"},
                "chat": {"id": chat_id, "type": "private"},
                "date": 1791108000,
                "text": text,
            },
        }
    )


def answered(notice: str | None = None) -> BotCall:
    params: dict[str, Any] = {"callback_query_id": "4382bfdwdsb323b2d9"}
    if notice is not None:
        params["text"] = notice
    return BotCall("answerCallbackQuery", params)


def shown(chat_id: int, text: str) -> BotCall:
    """The question, edited to show the answer instead of its buttons."""
    return BotCall("editMessageText", {"chat_id": chat_id, "message_id": QUESTION_ID, "text": text})


async def state(session: AsyncSession, entry_id: uuid.UUID) -> tuple[Any, ...]:
    entry = await session.get(JournalEntry, entry_id, populate_existing=True)
    assert entry is not None
    return entry.role_id, entry.new_role, entry.naming_role


async def journaled(session: AsyncSession, chat_id: int) -> int:
    count = select(func.count()).where(JournalMessage.chat_id == chat_id)
    return await session.scalar(count) or 0


async def test_a_roles_button_chooses_it_and_lets_the_jobs_run(
    session: AsyncSession, chat_id: int
) -> None:
    _, entry_id = await asked_entry(session, chat_id)
    handled = await handle_update(session, press(chat_id, "contoso"))
    assert handled == Handled(
        (
            answered(),
            shown(chat_id, "Role for this entry: Research Intern, Contoso Research (2024-2024)"),
        ),
        ready=(entry_id,),
    )
    assert await state(session, entry_id) == ("contoso", None, False)


async def test_a_new_role_is_named_by_the_next_message(session: AsyncSession, chat_id: int) -> None:
    """The name is stored with the entry, not journaled."""
    _, entry_id = await asked_entry(session, chat_id)
    handled = await handle_update(session, press(chat_id, NEW_ROLE))
    prompt = BotCall(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": NAME_PROMPT,
            "reply_markup": {"force_reply": True, "input_field_placeholder": "Title, organisation"},
        },
    )
    assert handled == Handled((answered(), prompt))
    assert await state(session, entry_id) == (None, None, True)

    handled = await handle_update(session, message(chat_id, f"  {NEW_NAME} "))
    assert handled == Handled(
        (
            BotCall(
                "sendMessage",
                {"chat_id": chat_id, "text": f"New role for this entry: {NEW_NAME}."},
            ),
            shown(chat_id, f"Role for this entry: {NEW_NAME} (new)"),
        ),
        ready=(entry_id,),
    )
    assert await state(session, entry_id) == (None, NEW_NAME, False)
    assert await journaled(session, chat_id) == 0

    await handle_update(session, message(chat_id, "Shipped the export fix.", message_id=51))
    assert await journaled(session, chat_id) == 1, "the next message is journaled again"


@pytest.mark.parametrize("name", ["Data Engineer\nFabrikam, since March", "x" * (NAME_LIMIT + 1)])
async def test_a_name_that_isnt_one_short_line_is_refused(
    session: AsyncSession, chat_id: int, name: str
) -> None:
    _, entry_id = await asked_entry(session, chat_id)
    await handle_update(session, press(chat_id, NEW_ROLE))
    handled = await handle_update(session, message(chat_id, name))
    assert handled == Handled(
        (BotCall("sendMessage", {"chat_id": chat_id, "text": NAME_TOO_LONG}),)
    )
    assert await state(session, entry_id) == (None, None, True), "it still waits for the name"


async def test_commands_still_work_while_a_name_is_awaited(
    session: AsyncSession, chat_id: int
) -> None:
    _, entry_id = await asked_entry(session, chat_id)
    await handle_update(session, press(chat_id, NEW_ROLE))
    handled = await handle_update(session, message(chat_id, "/help"))
    help_ = help_text(timedelta(minutes=30))
    assert handled == Handled((BotCall("sendMessage", {"chat_id": chat_id, "text": help_}),))
    assert await state(session, entry_id) == (None, None, True)


async def test_a_role_button_after_new_role_still_chooses_it(
    session: AsyncSession, chat_id: int
) -> None:
    """The owner changed their mind: the next message is journaled again."""
    _, entry_id = await asked_entry(session, chat_id)
    await handle_update(session, press(chat_id, NEW_ROLE))
    handled = await handle_update(session, press(chat_id, "northwind"))
    assert handled.ready == (entry_id,)
    assert await state(session, entry_id) == ("northwind", None, False)


async def test_only_the_latest_new_role_press_waits_for_a_name(
    session: AsyncSession, chat_id: int
) -> None:
    user_id, first = await asked_entry(session, chat_id)
    second = JournalEntry(
        user_id=user_id,
        opened_at=CLOSED,
        last_message_at=CLOSED,
        closed_at=CLOSED,
        closed_by="quiet",
        role_asked_at=CLOSED,
        role_question_id=QUESTION_ID + 1,
    )
    session.add(second)
    await session.flush()
    await handle_update(session, press(chat_id, NEW_ROLE))
    await handle_update(session, press(chat_id, NEW_ROLE, message_id=QUESTION_ID + 1))
    handled = await handle_update(session, message(chat_id, NEW_NAME))
    assert handled.ready == (second.id,)
    assert await state(session, first) == (None, None, False)
    assert await state(session, second.id) == (None, NEW_NAME, False)


@pytest.mark.parametrize(
    ("setup", "data", "notice"),
    [
        ({"role_id": "northwind"}, "contoso", ANSWERED),
        ({"new_role": NEW_NAME}, NEW_ROLE, ANSWERED),
        ({"role_question_id": QUESTION_ID - 1}, "contoso", GONE),
        ({}, "fabrikam", NOT_IN_PROFILE),
    ],
    ids=["answered", "named", "another message", "not in the profile"],
)
async def test_a_button_that_no_longer_applies_changes_nothing(
    session: AsyncSession, chat_id: int, setup: dict[str, Any], data: str, notice: str
) -> None:
    _, entry_id = await asked_entry(session, chat_id, **setup)
    before = await state(session, entry_id)
    handled = await handle_update(session, press(chat_id, data))
    assert handled == Handled((answered(notice),))
    assert await state(session, entry_id) == before


async def test_a_button_in_an_unlinked_chat_is_only_acknowledged(
    session: AsyncSession, chat_id: int
) -> None:
    handled = await handle_update(session, press(chat_id, "northwind"))
    assert handled == Handled((answered(),))
