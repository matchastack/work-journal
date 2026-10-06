"""`wj telegram set-webhook` and `wj telegram poll`, against a stand-in for Telegram."""

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx2
import pytest
from typer.testing import CliRunner

from app import cli as cli_module
from app.cli import cli
from app.config import Settings, get_settings

TOKEN = "123456:test-token-not-real"
runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def telegram(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx2.Request], httpx2.Response] | None = None,
) -> list[httpx2.Request]:
    """Stand in for Telegram, answering every request with `handler` or with success."""
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if handler is not None:
            return handler(request)
        return httpx2.Response(200, json={"ok": True, "result": True})

    transport = httpx2.MockTransport(handle)

    def client() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=transport)

    monkeypatch.setattr(cli_module, "_telegram_http", client)
    return requests


def configure(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


def test_set_webhook_points_telegram_at_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(
        monkeypatch,
        TELEGRAM_BOT_TOKEN=TOKEN,
        TELEGRAM_WEBHOOK_SECRET="webhook-secret",
        APP_URL="https://journal.example.com",
    )
    requests = telegram(monkeypatch)
    result = runner.invoke(cli, ["telegram", "set-webhook"])
    assert result.exit_code == 0, result.output
    assert "https://journal.example.com/telegram/webhook" in result.stdout
    [request] = requests
    assert request.url.path == f"/bot{TOKEN}/setWebhook"
    assert json.loads(request.content) == {
        "url": "https://journal.example.com/telegram/webhook",
        "secret_token": "webhook-secret",
        "allowed_updates": ["message", "edited_message"],
    }


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"TELEGRAM_BOT_TOKEN": TOKEN}, "Set TELEGRAM_WEBHOOK_SECRET"),
        ({"TELEGRAM_WEBHOOK_SECRET": "webhook-secret"}, "Telegram only sends updates to an https"),
        (
            {"TELEGRAM_WEBHOOK_SECRET": "webhook-secret", "APP_URL": "https://journal.example.com"},
            "Set TELEGRAM_BOT_TOKEN",
        ),
    ],
)
def test_set_webhook_needs_the_secret_https_and_the_token(
    monkeypatch: pytest.MonkeyPatch, values: dict[str, str], message: str
) -> None:
    configure(monkeypatch, **values)
    requests = telegram(monkeypatch)
    result = runner.invoke(cli, ["telegram", "set-webhook"])
    assert result.exit_code != 0
    assert message in result.output
    assert requests == []


def test_a_refusal_is_reported_without_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(
        monkeypatch,
        TELEGRAM_BOT_TOKEN=TOKEN,
        TELEGRAM_WEBHOOK_SECRET="webhook-secret",
        APP_URL="https://journal.example.com",
    )

    def refuse(request: httpx2.Request) -> httpx2.Response:
        reason = "Bad Request: bad webhook: failed to resolve host"
        return httpx2.Response(400, json={"ok": False, "description": reason})

    telegram(monkeypatch, refuse)
    result = runner.invoke(cli, ["telegram", "set-webhook"])
    assert result.exit_code == 1
    assert "refused setWebhook: Bad Request: bad webhook" in result.output
    assert TOKEN not in result.output


def test_poll_needs_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, TELEGRAM_BOT_TOKEN=TOKEN)
    requests = telegram(monkeypatch)
    result = runner.invoke(cli, ["telegram", "poll"])
    assert result.exit_code == 1
    assert "Set DATABASE_URL" in result.output
    assert requests == []
