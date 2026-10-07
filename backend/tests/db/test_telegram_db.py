"""The Telegram webhook against the test database: raw updates, journal messages and their edits,
linking, and entries."""

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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import (
    JournalEntry,
    JournalMessage,
    JournalMessageEdit,
    TelegramLink,
    TelegramUpdate,
    User,
)
from app.main import create_app
from app.telegram.api import BotApi
from app.telegram.entries import close_quiet_entries, entry_for
from app.telegram.journal import DONE, LINK_HINT, NOTHING_OPEN, UNKNOWN, help_text, purge_updates
from app.telegram.linking import (
    ALREADY_LINKED,
    EXPIRED,
    LINKED,
    LINKED_ELSEWHERE,
    new_link_token,
)
from app.telegram.polling import poll
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


@pytest.fixture(autouse=True)
def extracted(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """The entries the webhook hands on for extraction right after answering (FR-CAP-5). The jobs
    themselves are tested in `test_entry_processing_db.py`."""
    entries: list[uuid.UUID] = []

    async def record(entry_id: uuid.UUID) -> None:
        entries.append(entry_id)

    monkeypatch.setattr("app.telegram.webhook.extract_now", record)
    return entries


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


def new_user(database_url: str) -> uuid.UUID:
    [(user_id,)] = run(database_url, insert(User).returning(User.id))
    return user_id


def link(database_url: str, chat_id: int) -> uuid.UUID:
    user_id = new_user(database_url)
    run(database_url, insert(TelegramLink).values(user_id=user_id, chat_id=chat_id))
    return user_id


def link_token(database_url: str, user_id: uuid.UUID, *, made: datetime | None = None) -> str:
    """A one-time token for a link to the bot, made now or at `made`."""

    async def make() -> str:
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(engine)() as session, session.begin():
                token, _ = await new_link_token(session, user_id, now=made)
                return token
        finally:
            await engine.dispose()

    return asyncio.run(make())


def owner_of(database_url: str, chat_id: int) -> uuid.UUID | None:
    rows = run(database_url, select(TelegramLink.user_id).where(TelegramLink.chat_id == chat_id))
    return rows[0][0] if rows else None


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
    for name in ("photo_with_caption", "sticker", "group_message"):
        response = post(client, update(name, chat_id))
        assert (response.status_code, response.content) == (200, b""), name
    assert post(client, update("command", chat_id)).json()["text"].startswith("Send me notes")
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


def test_polling_journals_updates_as_the_webhook_does(database_url: str, chat_id: int) -> None:
    """`wj telegram poll`: the same storage, with replies sent through the Bot API."""
    link(database_url, chat_id)
    stranger = chat_id + 1
    batch = [json.loads(update("message", chat_id)), json.loads(update("message", stranger))]
    batches = [batch, []]
    calls: list[tuple[str, dict[str, Any]]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        method = request.url.path.rsplit("/", 1)[1]
        calls.append((method, json.loads(request.content)))
        result = batches.pop(0) if method == "getUpdates" else True
        return httpx2.Response(200, json={"ok": True, "result": result})

    async def run_poll() -> int:
        engine = create_async_engine(database_url)
        try:
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
                api = BotApi(SecretStr("123456:test-token-not-real"), http)
                sessions = async_sessionmaker(engine, expire_on_commit=False)
                return await poll(api, sessions, rounds=2, timeout=0)
        finally:
            await engine.dispose()

    assert asyncio.run(run_poll()) == 2
    assert messages(database_url, chat_id) == [(31, NOTE, None)]
    assert [method for method, _ in calls] == [
        "setMyCommands",
        "deleteWebhook",
        "getUpdates",
        "sendMessage",
        "getUpdates",
    ]
    assert [item["command"] for item in calls[0][1]["commands"]] == ["done", "help"]
    assert calls[3][1] == {"chat_id": stranger, "text": LINK_HINT}
    assert calls[4][1]["offset"] == batch[1]["update_id"] + 1, "confirms the updates handled"


def start(client: TestClient, chat_id: int, token: str) -> str:
    """Open a link to the bot in the chat, and return the bot's answer."""
    response = post(client, update("message", chat_id, text=f"/start {token}"))
    return response.json()["text"]


def test_a_link_to_the_bot_links_the_chat_once(
    client: TestClient, database_url: str, chat_id: int
) -> None:
    """FR-CAP-3: the token works once, and the `/start` message isn't journaled."""
    user_id = new_user(database_url)
    token = link_token(database_url, user_id)
    assert start(client, chat_id, token) == LINKED
    assert owner_of(database_url, chat_id) == user_id
    assert start(client, chat_id, token) == EXPIRED
    post(client, update("message", chat_id))
    assert messages(database_url, chat_id) == [(31, NOTE, None)]


def test_a_link_expires_after_15_minutes(
    client: TestClient, database_url: str, chat_id: int
) -> None:
    made = datetime.now(UTC) - timedelta(minutes=15, seconds=1)
    token = link_token(database_url, new_user(database_url), made=made)
    assert start(client, chat_id, token) == EXPIRED
    assert owner_of(database_url, chat_id) is None


def test_a_chat_linked_to_someone_else_stays_theirs(
    client: TestClient, database_url: str, chat_id: int
) -> None:
    first = link(database_url, chat_id)
    token = link_token(database_url, new_user(database_url))
    assert start(client, chat_id, token) == LINKED_ELSEWHERE
    assert owner_of(database_url, chat_id) == first


def test_linking_another_chat_moves_the_link(
    client: TestClient, database_url: str, chat_id: int
) -> None:
    """FR-SET-1: relinking. The old chat is then told how to link, like any other."""
    user_id = link(database_url, chat_id)
    assert start(client, chat_id, link_token(database_url, user_id)) == ALREADY_LINKED
    new_chat = chat_id + 1
    assert start(client, new_chat, link_token(database_url, user_id)) == LINKED
    assert (owner_of(database_url, chat_id), owner_of(database_url, new_chat)) == (None, user_id)
    assert post(client, update("message", chat_id)).json()["text"] == LINK_HINT


# --- Entries (FR-CAP-4) -------------------------------------------------------------------------

NINE = 1791104400
"""09:00 UTC on 2026-10-04, when the recorded message was sent."""


def entries(database_url: str, chat_id: int) -> list[tuple[int, uuid.UUID, str | None]]:
    """Each message's number, entry, and how its entry closed (None while it's open)."""
    query = (
        select(JournalMessage.message_id, JournalEntry.id, JournalEntry.closed_by)
        .join(JournalEntry, JournalEntry.id == JournalMessage.entry_id)
        .where(JournalMessage.chat_id == chat_id)
        .order_by(JournalMessage.message_id)
    )
    return [tuple(row) for row in run(database_url, query)]


def send(client: TestClient, chat_id: int, message_id: int, at: int, text: str = "A note.") -> str:
    """Send a message at Unix time `at`, and return the bot's answer, if any."""
    response = post(client, update("message", chat_id, message_id=message_id, date=at, text=text))
    return response.json()["text"] if response.content else ""


def test_messages_30_minutes_apart_start_a_new_entry(
    client: TestClient, database_url: str, chat_id: int, extracted: list[uuid.UUID]
) -> None:
    """A gap just under the timeout joins the entry; a gap of exactly 30 minutes starts a new
    one, and closes the quiet one as of when it went quiet. The closed one goes on to extraction
    right away."""
    link(database_url, chat_id)
    send(client, chat_id, 1, NINE)
    send(client, chat_id, 2, NINE + 30 * 60 - 1)
    send(client, chat_id, 3, NINE + 60 * 60 - 1)
    (_, first, closed_by), (_, same, _), (_, second, still_open) = entries(database_url, chat_id)
    assert (first == same, first != second) == (True, True)
    assert (closed_by, still_open) == ("quiet", None)
    closed_at = run(database_url, select(JournalEntry.closed_at).where(JournalEntry.id == first))
    assert closed_at == [(datetime.fromtimestamp(NINE + 60 * 60 - 1, UTC),)]
    assert extracted == [first]


def test_done_closes_the_entry_at_once(
    client: TestClient, database_url: str, chat_id: int, extracted: list[uuid.UUID]
) -> None:
    """The closed entry goes on to extraction right after the answer (FR-CAP-5)."""
    link(database_url, chat_id)
    send(client, chat_id, 1, NINE)
    assert send(client, chat_id, 2, NINE + 60, "/done") == DONE
    assert send(client, chat_id, 3, NINE + 120, "/done") == NOTHING_OPEN
    send(client, chat_id, 4, NINE + 180)
    (_, first, how), (_, second, _) = entries(database_url, chat_id)
    assert (how, first != second) == ("done", True), "the next message starts a new entry"
    closed_at = run(database_url, select(JournalEntry.closed_at).where(JournalEntry.id == first))
    assert closed_at == [(datetime.fromtimestamp(NINE + 60, UTC),)], "when /done was sent"
    assert extracted == [first], "only the update that closed an entry hands it on"


@pytest.mark.parametrize("command", ["/help", "/help@wj_example_bot", "/start", "/HELP extra"])
def test_help_lists_the_commands(
    client: TestClient, database_url: str, chat_id: int, command: str
) -> None:
    """FR-CAP-7: `/help` lists every command. `/start` without a link token does too."""
    link(database_url, chat_id)
    answer = send(client, chat_id, 1, NINE, command)
    assert answer == help_text(timedelta(minutes=30))
    assert "30 minutes after the last one starts a new entry" in answer
    assert "/done - close the current entry now" in answer
    assert "/help - list the commands" in answer
    assert entries(database_url, chat_id) == []


def test_an_unknown_command_is_not_journaled(
    client: TestClient, database_url: str, chat_id: int
) -> None:
    """A note that happens to start with "/" isn't lost silently: the answer says so."""
    link(database_url, chat_id)
    answer = send(client, chat_id, 1, NINE, "/etc files moved to the new config store")
    assert answer == f"{UNKNOWN}\n\n{help_text(timedelta(minutes=30))}"
    assert messages(database_url, chat_id) == []


def test_an_edit_to_a_closed_entry_changes_only_its_message(
    client: TestClient, database_url: str, chat_id: int
) -> None:
    """The entry stays closed, and the edit doesn't join the open one."""
    link(database_url, chat_id)
    post(client, update("message", chat_id))
    send(client, chat_id, 40, NINE + 60 * 60)
    post(client, update("edited_message", chat_id))
    assert [text for _, text, _ in messages(database_url, chat_id)] == [EDITED, "A note."]
    (_, first, how), (_, second, still_open) = entries(database_url, chat_id)
    assert (first != second, how, still_open) == (True, "quiet", None)


def test_the_timeout_can_be_configured(database_url: str, chat_id: int) -> None:
    settings = Settings(
        database_url=SecretStr(database_url),
        telegram_webhook_secret=SecretStr(SECRET),
        entry_timeout_minutes=5,
    )
    link(database_url, chat_id)
    with TestClient(create_app(settings)) as client:
        send(client, chat_id, 1, NINE)
        send(client, chat_id, 2, NINE + 5 * 60)
        assert "5 minutes" in send(client, chat_id, 3, NINE + 6 * 60, "/help")
    (_, first, _), (_, second, _) = entries(database_url, chat_id)
    assert first != second


def test_polling_hands_on_the_entry_an_update_closes(database_url: str, chat_id: int) -> None:
    """As with the webhook, `/done` while polling runs extraction right away (FR-CAP-5)."""
    link(database_url, chat_id)
    note = json.loads(update("message", chat_id, message_id=1, date=NINE))
    done = json.loads(update("message", chat_id, message_id=2, date=NINE + 60, text="/done"))
    batches = [[note, done], []]
    handed_on: list[uuid.UUID] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        method = request.url.path.rsplit("/", 1)[1]
        result = batches.pop(0) if method == "getUpdates" else True
        return httpx2.Response(200, json={"ok": True, "result": result})

    async def after_close(entry_id: uuid.UUID) -> None:
        handed_on.append(entry_id)

    async def run_poll() -> int:
        engine = create_async_engine(database_url)
        try:
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
                api = BotApi(SecretStr("123456:test-token-not-real"), http)
                sessions = async_sessionmaker(engine, expire_on_commit=False)
                return await poll(api, sessions, rounds=2, timeout=0, after_close=after_close)
        finally:
            await engine.dispose()

    assert asyncio.run(run_poll()) == 2
    [(_, entry_id, how)] = entries(database_url, chat_id)
    assert (handed_on, how) == ([entry_id], "done")


@pytest.mark.anyio
async def test_the_tick_closes_entries_that_went_quiet(session: AsyncSession) -> None:
    """At exactly the timeout an entry closes; a minute less, it stays open."""
    timeout = timedelta(minutes=30)
    now = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    users = []
    for _ in range(2):
        user = User()
        session.add(user)
        await session.flush()
        users.append(user.id)
    quiet, _ = await entry_for(session, users[0], now - timeout, timeout)
    recent, _ = await entry_for(session, users[1], now - timeout + timedelta(minutes=1), timeout)
    closed = await close_quiet_entries(session, now, timeout)
    assert quiet in closed and recent not in closed
    states = await session.execute(
        select(JournalEntry.id, JournalEntry.closed_at, JournalEntry.closed_by).where(
            JournalEntry.id.in_([quiet, recent])
        )
    )
    assert {row.id: (row.closed_at, row.closed_by) for row in states} == {
        quiet: (now, "quiet"),
        recent: (None, None),
    }
