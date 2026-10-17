"""A stand-in for the SDK's `client.beta.messages`, for unit tests: scripted replies, no network.

    fake = FakeMessages(reply(Facts(...)))
    client = LLMClient(settings, fake, MemoryCallLog())
    client.call("fact_extraction", prompt, Facts, variables={"entry": "..."})
    fake.requests[0]["model"]

Scripted replies answer requests in the order they arrive. Calls made from several threads at
once, such as a refresh's bullets (`app/synthesis.py`), arrive in no set order, so a test of those
answers each request by what it asks instead:

    def answer(request):
        if output_name(request) == "WrittenBullet":
            return reply(WrittenBullet(text=...))
        ...

    fake = FakeMessages(answer=answer)

A refresh's job and `wj propose` write the bullets after the first `REFRESH_CONCURRENCY` at a
time, so a test of either that plans more than two bullets answers by request, or sets
`REFRESH_CONCURRENCY=1`.

Replies are built as the API sends them and validated by the SDK's own models.
"""

import copy
import threading
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self

from anthropic import Omit
from anthropic.types.beta import BetaMessage, BetaStopReason
from pydantic import BaseModel

FAKE_MODEL = "fake-model"

type Answer = Callable[[dict[str, Any]], BetaMessage | Exception]
"""Replies to one request, given its arguments, with a message or an error to raise."""


class FakeMessages:
    """Answers each request with the next scripted reply, or with what `answer` replies to it: a
    message, or an error to raise.

    Several threads may call it at once. `answer` runs outside its lock, so it may sleep or wait,
    as a slow reply would.
    """

    def __init__(self, *replies: BetaMessage | Exception, answer: Answer | None = None) -> None:
        if replies and answer is not None:
            raise ValueError("answer requests with scripted replies or with `answer`, not both")
        self.replies = list(replies)
        self.answer = answer
        self.requests: list[dict[str, Any]] = []
        """Each request's arguments as the SDK received them, without the omitted ones."""
        self._lock = threading.Lock()

    def stream(self, **request: Any) -> "FakeStream":
        sent = {key: value for key, value in request.items() if not isinstance(value, Omit)}
        sent = copy.deepcopy(sent)
        scripted: BetaMessage | Exception | None = None
        with self._lock:
            self.requests.append(sent)
            request_id = f"req_fake_{len(self.requests)}"
            if self.answer is None and self.replies:
                scripted = self.replies.pop(0)
        if self.answer is not None:
            return FakeStream(self.answer(sent), request_id=request_id)
        if scripted is None:
            raise AssertionError("FakeMessages has no scripted reply left")
        return FakeStream(scripted, request_id=request_id)


def output_name(request: dict[str, Any]) -> str:
    """The name of the output model a request asks for, such as `WrittenBullet`."""
    return request["output_config"]["format"]["schema"]["title"]


def prompt_text(request: dict[str, Any]) -> str:
    """A request's first user message: the prompt's rendered user template."""
    return request["messages"][0]["content"]


class FakeStream:
    def __init__(self, reply: BetaMessage | Exception, request_id: str) -> None:
        self._reply = reply
        self._request_id = request_id

    def __enter__(self) -> Self:
        if isinstance(self._reply, Exception):
            raise self._reply
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    @property
    def request_id(self) -> str | None:
        return self._request_id

    def get_final_message(self) -> BetaMessage:
        assert isinstance(self._reply, BetaMessage)
        return self._reply


def reply(
    output: BaseModel | str,
    *,
    stop_reason: BetaStopReason = "end_turn",
    model: str = FAKE_MODEL,
    input_tokens: int = 100,
    output_tokens: int = 20,
    cache_read: int = 0,
    cache_write: int = 0,
) -> BetaMessage:
    """A reply whose text is `output`; a model is sent as JSON, the way Claude would write it."""
    text = output if isinstance(output, str) else output.model_dump_json(by_alias=True)
    return _message(
        content=[{"type": "text", "text": text}],
        stop_reason=stop_reason,
        model=model,
        usage={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
        },
    )


def refusal(
    category: str | None = "cyber", explanation: str | None = "The request was declined."
) -> BetaMessage:
    """A reply that declines the request, with no output."""
    return _message(
        content=[],
        stop_reason="refusal",
        model=FAKE_MODEL,
        usage={"input_tokens": 100, "output_tokens": 0},
        stop_details={"type": "refusal", "category": category, "explanation": explanation},
    )


def fallback_reply(output: BaseModel, *, declined_by: str, served_by: str) -> BetaMessage:
    """A reply from the API's fallback model, after `declined_by` refused before any output."""
    declined = {"input_tokens": 100, "output_tokens": 0}
    served = {"input_tokens": 100, "output_tokens": 20}
    cache = {"cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    return _message(
        content=[
            {
                "type": "fallback",
                "from": {"model": declined_by},
                "to": {"model": served_by},
                "trigger": {"type": "refusal", "category": "cyber"},
            },
            {"type": "text", "text": output.model_dump_json(by_alias=True)},
        ],
        stop_reason="end_turn",
        model=served_by,
        usage={
            **served,
            "iterations": [
                {"type": "message", "model": declined_by, **declined, **cache},
                {"type": "fallback_message", "model": served_by, **served, **cache},
            ],
        },
    )


def _message(
    *,
    content: list[dict[str, Any]],
    stop_reason: BetaStopReason,
    model: str,
    usage: dict[str, Any],
    stop_details: dict[str, Any] | None = None,
) -> BetaMessage:
    return BetaMessage.model_validate(
        {
            "id": "msg_fake",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": stop_reason,
            "stop_details": stop_details,
            "stop_sequence": None,
            "usage": usage,
        }
    )
