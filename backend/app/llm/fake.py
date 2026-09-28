"""A stand-in for the SDK's `client.beta.messages`, for unit tests: scripted replies, no network.

    fake = FakeMessages(reply(Facts(...)))
    client = LLMClient(settings, fake, MemoryCallLog())
    client.call("fact_extraction", prompt, Facts, variables={"entry": "..."})
    fake.requests[0]["model"]

Replies are built as the API sends them and validated by the SDK's own models.
"""

import copy
from types import TracebackType
from typing import Any, Self

from anthropic import Omit
from anthropic.types.beta import BetaMessage, BetaStopReason
from pydantic import BaseModel

FAKE_MODEL = "fake-model"


class FakeMessages:
    """Answers each request with the next scripted reply: a message, or an error to raise."""

    def __init__(self, *replies: BetaMessage | Exception) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        """Each request's arguments as the SDK received them, without the omitted ones."""

    def stream(self, **request: Any) -> "FakeStream":
        sent = {key: value for key, value in request.items() if not isinstance(value, Omit)}
        self.requests.append(copy.deepcopy(sent))
        if not self.replies:
            raise AssertionError("FakeMessages has no scripted reply left")
        return FakeStream(self.replies.pop(0), request_id=f"req_fake_{len(self.requests)}")


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
