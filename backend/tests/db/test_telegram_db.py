"""The Telegram webhook against the test database: raw updates, journal messages and edits."""

import asyncio
import json
import random
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Executable, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import Settings
from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import JournalMessage, JournalMessageEdit, TelegramLink, TelegramUpdate, User
from app.main import create_app
from app.telegram.journal import LINK_HINT, purge_updates
from app.telegram.webhook import SECRET_HEADER

FIXTURES = Path(__file__).parents[1] / "fixtures" / "telegram"
SECRET = "test-webhook-secret"
NOTE = "Moved the nightly export onto a queue today, it finishes in 12 minutes instead of 50."
EDITED = "Moved the nightly export onto a queue today; it now finishes in 12 minutes instead of 50."
_random = random.SystemRandom()


@pytest.fixture(autouse=True)
def key_ring() -> Iterator[None]:
    use_key_ring(KeyRing.parse(new_key("k1")))
    yield
    use_key_ring(None)


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    settings = Settings(
        database_url=SecretStr(database_url), telegram_webhook_secret=SecretStr(SECRET)
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def chat_id() -> int:
    """A private chat of the test's own: the test database is shared by the whole session."""
    return _random.randrange(10**9, 10**12)


def run(database_url: str, statement: Executable) -> list[Any]:
    """Run one statement in its own transaction and return its rows."""

    async def execute() -> list[Any]:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                result = await connection.execute(statement)
                return list(result.all()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(execute())


def link(database_url: str, chat_id: int) -> uuid.UUID:
    [(user_id,)] = run(database_url, insert(User).returning(User.id))
    run(database_url, insert(TelegramLink).values(user_id=user_id, chat_id=chat_id))
    return user_id


def update(name: str, chat_id: int, **changes: Any) -> str:
    """A recorded update with a fresh `update_id`, sent from `chat_id` if it's a private chat."""
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    data["update_id"] = _random.randrange(10**9, 10**12)
    message = data.get("message") or data["edited_message"]
    if message["chat"]["type"] == "private":
        message["chat"]["id"] = message["from"]["id"] = chat_id
    message.update(changes)
    return json.dumps(data)


def post(client: TestClient, payload: str, secret: str | None = SECRET) -> httpx2.Response:
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers[SECRET_HEADER] = secret
    return client.post("/telegram/webhook", content=payload, headers=headers)


def messages(database_url: str, chat_id: int) -> list[tuple[int, str, datetime | None]]:
    query = (
        select(JournalMessage.message_id, JournalMessage.text, JournalMessage.edited_at)
        .where(JournalMessage.chat_id == chat_id)
        .order_by(JournalMessage.message_id)
    )
    return [tuple(row) for row in run(database_url, query)]


def stored_updates(database_url: str, payload: str) -> int:
    update_id = json.loads(payload)["update_id"]
    query = select(func.count()).where(TelegramUpdate.update_id == update_id)
    return run(database_url, query)[0][0]


@pytest.mark.parametrize("secret", [None, "wrong-secret"])
def test_a_request_without_the_secret_is_refused_and_not_stored(
    client: TestClient, database_url: str, chat_id: int, secret: str | None
) -> None:
    """FR-CAP-1: only Telegram knows the secret."""
    link(database_url, chat_id)
    payload = update("message", chat_id)
    assert post(client, payload, secret).status_code == 401
    assert stored_updates(database_url, payload) == 0
    assert messages(database_url, chat_id) == []


def test_without_a_secret_set_every_request_is_refused(database_url: str, chat_id: int) -> None:
    with TestClient(create_app(Settings(database_url=SecretStr(database_url)))) as client:
        assert post(client, update("message", chat_id), secret="").status_code == 401


def test_a_message_from_a_linked_chat_is_journaled_encrypted(
    client: TestClient, database_url: str, chat_id: int
) -> None:
    link(database_url, chat_id)
    payload = update("message", chat_id)
    response = post(client, payload)
    assert (response.status_code, response.content) == (200, b"")
    assert messages(database_url, chat_id) == [(31, NOTE, None)]
    update_id = json.loads(payload)["update_id"]
    [(raw_update,)] = run(
        database_url,
        text("SELECT payload FROM telegram_updates WHERE update_id = :id").bindparams(id=update_id),
    )
    [(raw_text,)] = run(
        database_url,
        text("SELECT text FROM journal_messages WHERE chat_id = :id").bindparams(id=chat_id),
    )
    assert b"nightly export" not in bytes(raw_update) + bytes(raw_text), "FR-JRN-4"


def test_an_update_sent_twice_is_stored_once(
    client: TestClient, database_url: str, chat_id: int
) -> None:
    """FR-JRN-1: Telegram resends an update until the webhook answers."""
    link(database_url, chat_id)
    payload = update("message", chat_id)
    assert [post(client, payload).status_code for _ in range(2)] == [200, 200]
    assert stored_updates(database_url, payload) == 1
    assert len(messages(database_url, chat_id)) == 1


def test_an_edit_keeps_the_earlier_text(
    client: TestClient, database_url: str, chat_id: int
) -> None:
    """FR-JRN-3. An edit that arrives after a newer one is ignored."""
    user_id = link(database_url, chat_id)
    post(client, update("message", chat_id))
    post(client, update("edited_message", chat_id))
    edited_at = datetime(2026, 10, 4, 9, 5, tzinfo=UTC)
    assert messages(database_url, chat_id) == [(31, EDITED, edited_at)]
    earlier = select(JournalMessageEdit.text, JournalMessageEdit.replaced_at).where(
        JournalMessageEdit.user_id == user_id
    )
    assert [tuple(row) for row in run(database_url, earlier)] == [(NOTE, edited_at)]
    stale = update("edited_message", chat_id, text="An older edit.", edit_date=1791104580)
    post(client, stale)
    assert messages(database_url, chat_id) == [(31, EDITED, edited_at)]


def test_an_unlinked_chat_is_told_how_to_link_and_nothing_is_journaled(
    client: TestClient, database_url: str, chat_id: int
) -> None:
    """FR-CAP-2. The reply rides on the webhook's answer, so it needs no request of its own."""
    payload = update("message", chat_id)
    response = post(client, payload)
    assert response.json() == {"method": "sendMessage", "chat_id": chat_id, "text": LINK_HINT}
    assert stored_updates(database_url, payload) == 1
    assert messages(database_url, chat_id) == []


def test_only_text_and_captions_from_the_linked_chat_are_journaled(
    client: TestClient, database_url: str, chat_id: int
) -> None:
    """A photo's caption is kept. Commands, stickers and group chats aren't journal messages."""
    link(database_url, chat_id)
    for name in ("photo_with_caption", "command", "sticker", "group_message"):
        response = post(client, update(name, chat_id))
        assert (response.status_code, response.content) == (200, b""), name
    assert messages(database_url, chat_id) == [(32, "The dashboard after the queue change.", None)]


@pytest.mark.parametrize("payload", ["not json", '{"message": {"text": "no update id"}}'])
def test_an_update_the_bot_cant_read_is_skipped(client: TestClient, payload: str) -> None:
    """Answering with an error would only make Telegram send it again."""
    assert post(client, payload).status_code == 200


@pytest.mark.anyio
async def test_raw_updates_are_deleted_after_seven_days(session: AsyncSession) -> None:
    """FR-JRN-2. Journal messages stay."""
    now = datetime(2026, 10, 12, 3, 17, tzinfo=UTC)
    old, recent = _random.randrange(10**9, 10**12), _random.randrange(10**9, 10**12)
    for update_id, age in ((old, timedelta(days=8)), (recent, timedelta(days=6))):
        session.add(TelegramUpdate(update_id=update_id, payload="{}", received_at=now - age))
    await session.flush()
    assert await purge_updates(session, before=now - timedelta(days=7)) >= 1
    remaining = select(TelegramUpdate.update_id).where(TelegramUpdate.update_id.in_([old, recent]))
    assert list(await session.scalars(remaining)) == [recent]
