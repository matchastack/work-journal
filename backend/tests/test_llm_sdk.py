"""The client on the real SDK, with HTTP answered locally: what goes over the wire, and back."""

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from anthropic import Anthropic
from pydantic import Field

from app.config import Settings
from app.llm.client import FALLBACK_BETA, LLMClient, LLMRequestError, LLMUnavailable
from app.llm.prompts import load_prompt
from app.llm.usage import MemoryCallLog
from app.schema.common import Model, NonEmptyStr

PROMPT = load_prompt("triage", 1, root=Path(__file__).parent / "fixtures" / "prompts")
VARIABLES = {"sender": "Casey", "message": "Moved the nightly import onto a queue."}


class Triage(Model):
    work_related: bool
    topics: tuple[NonEmptyStr, ...] = Field(default=(), max_length=3)


class Server:
    """Answers each request with the next response, and keeps the requests."""

    def __init__(self, *responses: httpx2.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    def body(self, index: int = 0) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def make(server: Server) -> tuple[LLMClient, MemoryCallLog]:
    http = httpx2.Client(transport=httpx2.MockTransport(server))
    sdk = Anthropic(api_key="sk-test", http_client=http, max_retries=0)
    settings = Settings(llm_model_heavy="heavy-model", llm_model_light="light-model")
    log = MemoryCallLog()
    return LLMClient(settings, sdk.beta.messages, log), log


def events(*items: dict[str, Any]) -> httpx2.Response:
    stream = "".join(f"event: {item['type']}\ndata: {json.dumps(item)}\n\n" for item in items)
    headers = {"content-type": "text/event-stream", "request-id": "req_1"}
    return httpx2.Response(200, headers=headers, content=stream.encode())


def message_start(model: str) -> dict[str, Any]:
    usage = {"input_tokens": 50, "output_tokens": 1, "cache_read_input_tokens": 400}
    message = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": usage,
    }
    return {"type": "message_start", "message": message}


def streamed(text: str, model: str = "model-that-answered") -> httpx2.Response:
    return events(
        message_start(model),
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 12},
        },
        {"type": "message_stop"},
    )


def error(status: int, error_type: str) -> httpx2.Response:
    body = {"type": "error", "error": {"type": error_type, "message": "Something went wrong."}}
    return httpx2.Response(status, json=body)


def test_a_heavy_call_on_the_wire() -> None:
    server = Server(streamed('{"workRelated": true, "topics": ["queues"]}'))
    llm, log = make(server)
    result = llm.call("profile_synthesis", PROMPT, Triage, variables=VARIABLES, context="{}")
    assert result.output == Triage(work_related=True, topics=("queues",))
    request = server.requests[0]
    assert request.url.path == "/v1/messages"
    assert FALLBACK_BETA in request.headers["anthropic-beta"].split(",")
    body = server.body()
    assert (body["model"], body["stream"], body["fallbacks"]) == ("heavy-model", True, "default")
    assert [block["cache_control"] for block in body["system"]] == [{"type": "ephemeral"}] * 2
    assert body["output_config"]["format"]["type"] == "json_schema"
    call = log.calls[0]
    assert (call.served_by, call.request_id) == ("model-that-answered", "req_1")
    assert (call.input_tokens, call.cache_read_tokens, call.output_tokens) == (50, 400, 12)


def test_a_light_call_sends_no_beta_or_fallback() -> None:
    server = Server(streamed('{"workRelated": false}'))
    llm, _ = make(server)
    result = llm.call("message_triage", PROMPT, Triage, variables=VARIABLES)
    assert result.output == Triage(work_related=False)
    assert "anthropic-beta" not in server.requests[0].headers
    assert "fallbacks" not in server.body()


def test_an_overloaded_api_is_unavailable() -> None:
    llm, log = make(Server(error(529, "overloaded_error")))
    with pytest.raises(LLMUnavailable, match=r"OverloadedError \(529\)"):
        llm.call("message_triage", PROMPT, Triage, variables=VARIABLES)
    assert log.calls[0].outcome == "unavailable"


def test_an_error_midway_through_the_stream_is_unavailable() -> None:
    failed = events(
        message_start("light-model"),
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    )
    llm, log = make(Server(failed))
    with pytest.raises(LLMUnavailable):
        llm.call("message_triage", PROMPT, Triage, variables=VARIABLES)
    assert log.calls[0].error == "APIStatusError (200)"


def test_an_unknown_model_is_rejected() -> None:
    llm, log = make(Server(error(404, "not_found_error")))
    with pytest.raises(LLMRequestError, match=r"NotFoundError \(404\)"):
        llm.call("message_triage", PROMPT, Triage, variables=VARIABLES)
    assert log.calls[0].outcome == "rejected"
