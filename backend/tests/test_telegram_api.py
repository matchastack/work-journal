"""The Bot API client, against a stand-in for api.telegram.org."""

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr

from app.telegram.api import BotApi, BotUser, TelegramError

TOKEN = "123456:test-token-not-real"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def bot(handler: Callable[[httpx2.Request], httpx2.Response]) -> BotApi:
    return BotApi(SecretStr(TOKEN), httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))


def ok(result: Any) -> httpx2.Response:
    return httpx2.Response(200, json={"ok": True, "result": result})


async def test_each_method_posts_json_to_the_bots_address() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return ok(True)

    api = bot(handle)
    await api.send_message(4242, "Noted.")
    await api.set_webhook("https://journal.example.com/telegram/webhook", "s3cret")
    await api.set_my_commands([("done", "close the current entry now")])
    await api.delete_webhook()
    assert [request.url.path for request in requests] == [
        f"/bot{TOKEN}/sendMessage",
        f"/bot{TOKEN}/setWebhook",
        f"/bot{TOKEN}/setMyCommands",
        f"/bot{TOKEN}/deleteWebhook",
    ]
    assert [json.loads(request.content) for request in requests] == [
        {"chat_id": 4242, "text": "Noted."},
        {
            "url": "https://journal.example.com/telegram/webhook",
            "secret_token": "s3cret",
            "allowed_updates": ["message", "edited_message"],
        },
        {"commands": [{"command": "done", "description": "close the current entry now"}]},
        {},
    ]


async def test_the_bots_account_and_updates_are_read() -> None:
    updates = [{"update_id": 10}, {"update_id": 11}]

    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/getMe"):
            return ok({"id": 99, "is_bot": True, "first_name": "Journal", "username": "wj_bot"})
        assert json.loads(request.content) == {
            "timeout": 30,
            "allowed_updates": ["message", "edited_message"],
            "offset": 10,
        }
        return ok(updates)

    api = bot(handle)
    assert await api.get_me() == BotUser(id=99, username="wj_bot")
    assert await api.get_updates(offset=10) == updates


async def test_a_refusal_is_an_error_with_telegrams_reason() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            400, json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
        )

    with pytest.raises(TelegramError, match="refused sendMessage: Bad Request: chat not found"):
        await bot(refuse).send_message(1, "Noted.")


def bad_gateway(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(502, text="<html>Bad gateway</html>")


def unreachable(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError(f"can't connect to {request.url}")


@pytest.mark.parametrize(
    ("handler", "message"),
    [(bad_gateway, "unexpected reply"), (unreachable, "couldn't be reached")],
)
async def test_errors_never_quote_the_token(
    handler: Callable[[httpx2.Request], httpx2.Response], message: str
) -> None:
    with pytest.raises(TelegramError, match=message) as error:
        await bot(handler).get_me()
    assert TOKEN not in str(error.value)
    assert error.value.__cause__ is None and error.value.__suppress_context__


async def test_the_request_log_never_shows_the_token(caplog: pytest.LogCaptureFixture) -> None:
    """httpx2 logs every request's address, and a Bot API address holds the token."""
    with caplog.at_level(logging.INFO, logger="httpx2"):
        await bot(lambda request: ok(True)).delete_webhook()
    assert "api.telegram.org/bot<token>/deleteWebhook" in caplog.text
    assert TOKEN not in caplog.text
