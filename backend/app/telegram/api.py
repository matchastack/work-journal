"""The few Telegram Bot API methods the app uses, called over HTTPS with httpx2.

Telegram's reference: https://core.telegram.org/bots/api. Each method is a POST to
`https://api.telegram.org/bot<token>/<method>`, and Telegram answers `{"ok": true, "result": ...}`
or `{"ok": false, "description": ...}`.

The token is part of every address, so it must never reach a log: `TokenFilter` removes it from
httpx2's request log, and errors never quote an address.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx2
from pydantic import BaseModel, SecretStr, ValidationError

API_URL = "https://api.telegram.org"
TIMEOUT_S = 10.0
POLL_TIMEOUT_S = 30
"""How long `getUpdates` waits for an update before answering with none (long polling)."""
ALLOWED_UPDATES = ("message", "edited_message")
"""The kinds of update the bot asks for. Telegram doesn't send the others."""
_TOKEN_IN_URL = re.compile(r"(api\.telegram\.org/bot)[^/\s]+")


class TelegramError(Exception):
    """Telegram couldn't be reached or refused a request. The message never holds the token or
    the text of a message."""


class BotUser(BaseModel):
    id: int
    username: str


class _Reply(BaseModel):
    ok: bool
    result: Any = None
    description: str | None = None


class TokenFilter(logging.Filter):
    """Removes bot tokens from httpx2's log of each request, which quotes the whole address."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "api.telegram.org/bot" in message:
            record.msg = _TOKEN_IN_URL.sub(r"\1<token>", message)
            record.args = None
        return True


logging.getLogger("httpx2").addFilter(TokenFilter())


@dataclass(frozen=True)
class BotApi:
    token: SecretStr
    http: httpx2.AsyncClient

    async def get_me(self) -> BotUser:
        """The bot's own account, whose username goes into links to the bot."""
        return BotUser.model_validate(await self._call("getMe"))

    async def send_message(self, chat_id: int, text: str) -> None:
        await self._call("sendMessage", chat_id=chat_id, text=text)

    async def set_webhook(self, url: str, secret_token: str) -> None:
        """Have Telegram send updates to `url`, with `secret_token` in a header."""
        allowed = list(ALLOWED_UPDATES)
        await self._call("setWebhook", url=url, secret_token=secret_token, allowed_updates=allowed)

    async def delete_webhook(self) -> None:
        """Stop sending updates to the webhook, so that `get_updates` can fetch them instead."""
        await self._call("deleteWebhook")

    async def get_updates(
        self, offset: int | None, timeout: int = POLL_TIMEOUT_S
    ) -> list[dict[str, Any]]:
        """The updates from `offset` on, waiting up to `timeout` seconds for the first one.
        Asking from an offset confirms the updates before it, which Telegram then forgets."""
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": list(ALLOWED_UPDATES)}
        if offset is not None:
            params["offset"] = offset
        result = await self._call("getUpdates", wait_s=timeout, **params)
        if not isinstance(result, list):
            raise TelegramError("Telegram sent an unexpected reply to getUpdates")
        return [update for update in result if isinstance(update, dict)]  # pyright: ignore[reportUnknownVariableType]

    async def _call(self, method: str, *, wait_s: float = 0, **params: Any) -> Any:
        url = f"{API_URL}/bot{self.token.get_secret_value()}/{method}"
        try:
            response = await self.http.post(url, json=params, timeout=TIMEOUT_S + wait_s)
        except httpx2.HTTPError as error:
            message = f"Telegram couldn't be reached for {method} ({type(error).__name__})"
            raise TelegramError(message) from None
        try:
            reply = _Reply.model_validate_json(response.content)
        except ValidationError:
            message = f"Telegram sent an unexpected reply to {method} (HTTP {response.status_code})"
            raise TelegramError(message) from None
        if not reply.ok:
            raise TelegramError(f"Telegram refused {method}: {reply.description or 'no reason'}")
        return reply.result
