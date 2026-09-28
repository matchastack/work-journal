from pathlib import Path
from typing import Any

import httpx2
import pytest
from anthropic import (
    APIConnectionError,
    APIStatusError,
    BadRequestError,
    OverloadedError,
    RateLimitError,
    transform_schema,
)
from anthropic.types.beta import BetaMessage
from pydantic import Field, SecretStr

from app.config import Settings
from app.llm.client import (
    FALLBACK_BETA,
    LLMClient,
    LLMConfigError,
    LLMInvalidOutput,
    LLMRefusal,
    LLMRequestError,
    LLMUnavailable,
)
from app.llm.fake import FAKE_MODEL, FakeMessages, fallback_reply, refusal, reply
from app.llm.prompts import load_prompt
from app.llm.usage import JsonlCallLog, MemoryCallLog
from app.schema.common import Model, NonEmptyStr

PROMPT = load_prompt("triage", 1, root=Path(__file__).parent / "fixtures" / "prompts")
VARIABLES = {"sender": "Casey", "message": "Moved the nightly import onto a queue."}
REQUEST_URL = "https://api.anthropic.com/v1/messages"


class Triage(Model):
    work_related: bool
    topics: tuple[NonEmptyStr, ...] = Field(default=(), max_length=3)


WORK = Triage(work_related=True, topics=("queues",))


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Settings come only from each test's arguments: no .env file and no environment."""
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def make(
    *replies: BetaMessage | Exception, **settings: Any
) -> tuple[LLMClient, FakeMessages, MemoryCallLog]:
    values: dict[str, Any] = {
        "llm_model_heavy": "heavy-model",
        "llm_model_standard": "standard-model",
        "llm_model_light": "light-model",
        "llm_price_light": (1, 5),
    }
    fake, log = FakeMessages(*replies), MemoryCallLog()
    return LLMClient(Settings(**(values | settings)), fake, log), fake, log


def api_error(kind: type[APIStatusError], status: int, error_type: str) -> APIStatusError:
    body = {"type": "error", "error": {"type": error_type, "message": "Something went wrong."}}
    request = httpx2.Request("POST", REQUEST_URL)
    response = httpx2.Response(status, request=request, json=body, headers={"request-id": "req_9"})
    return kind("Something went wrong.", response=response, body=body)


def test_the_task_tier_picks_the_model() -> None:
    llm, fake, _ = make(reply(WORK), reply(WORK), reply(WORK))
    llm.call("profile_synthesis", PROMPT, Triage, variables=VARIABLES)
    llm.call("fact_extraction", PROMPT, Triage, variables=VARIABLES)
    llm.call("message_triage", PROMPT, Triage, variables=VARIABLES)
    assert [request["model"] for request in fake.requests] == [
        "heavy-model",
        "standard-model",
        "light-model",
    ]


def test_the_reply_is_parsed_into_the_output_model() -> None:
    llm, fake, _ = make(reply(WORK))
    result = llm.call("message_triage", PROMPT, Triage, variables=VARIABLES)
    assert result.output == WORK
    request = fake.requests[0]
    assert request["messages"] == [{"role": "user", "content": PROMPT.render(VARIABLES)}]
    assert request["output_config"] == {
        "format": {"type": "json_schema", "schema": transform_schema(Triage)}
    }


def test_a_tier_without_a_model_sends_nothing() -> None:
    llm, fake, log = make(reply(WORK), llm_model_standard=None)
    with pytest.raises(LLMConfigError, match="LLM_MODEL_STANDARD"):
        llm.call("fact_extraction", PROMPT, Triage, variables=VARIABLES)
    assert fake.requests == []
    assert log.calls == []


def test_instructions_and_context_are_cached_prefixes() -> None:
    llm, fake, _ = make(reply(WORK), reply(WORK))
    llm.call("message_triage", PROMPT, Triage, variables=VARIABLES)
    llm.call("message_triage", PROMPT, Triage, variables=VARIABLES, context='{"work": []}')
    cached = {"type": "ephemeral"}
    instructions = {"type": "text", "text": PROMPT.system, "cache_control": cached}
    assert fake.requests[0]["system"] == [instructions]
    assert fake.requests[1]["system"] == [
        instructions,
        {"type": "text", "text": '{"work": []}', "cache_control": cached},
    ]


def test_only_heavy_tasks_ask_for_a_fallback_model() -> None:
    llm, fake, _ = make(reply(WORK), reply(WORK))
    llm.call("tailoring_selection", PROMPT, Triage, variables=VARIABLES)
    llm.call("bullet_writing", PROMPT, Triage, variables=VARIABLES)
    heavy, standard = fake.requests
    assert heavy["betas"] == [FALLBACK_BETA]
    assert heavy["fallbacks"] == "default"
    assert "betas" not in standard
    assert "fallbacks" not in standard


def test_the_heavy_fallback_can_be_turned_off() -> None:
    llm, fake, _ = make(reply(WORK), llm_heavy_fallback=False)
    llm.call("profile_synthesis", PROMPT, Triage, variables=VARIABLES)
    assert "betas" not in fake.requests[0]
    assert "fallbacks" not in fake.requests[0]


def test_each_call_is_recorded_with_tokens_cost_and_latency() -> None:
    llm, _, log = make(reply(WORK, input_tokens=1000, output_tokens=200, cache_read=4000))
    result = llm.call("message_triage", PROMPT, Triage, variables=VARIABLES)
    assert log.calls == [result.call]
    call = result.call
    assert (call.task, call.tier, call.model, call.served_by) == (
        "message_triage",
        "light",
        "light-model",
        FAKE_MODEL,
    )
    assert (call.prompt, call.outcome, call.attempts) == ("triage/v1", "ok", 1)
    assert not call.fell_back
    assert (call.input_tokens, call.output_tokens, call.cache_read_tokens) == (1000, 200, 4000)
    # (1,000 input + 4,000 cached x 0.1) x $1 + 200 output x $5, per million tokens
    assert call.cost_usd == pytest.approx(0.0024)
    assert call.latency_ms >= 0
    assert call.request_id == "req_fake_1"
    assert call.error is None
    assert call.at.utcoffset() is not None


def test_a_tier_without_a_price_records_no_cost() -> None:
    llm, _, log = make(reply(WORK))
    llm.call("fact_extraction", PROMPT, Triage, variables=VARIABLES)
    assert log.calls[0].cost_usd is None


def test_invalid_output_is_retried_once_with_the_errors() -> None:
    invalid = '{"workRelated": "maybe"}'
    llm, fake, log = make(reply(invalid), reply(WORK))
    result = llm.call("message_triage", PROMPT, Triage, variables=VARIABLES)
    assert result.output == WORK
    first, retry = fake.requests
    assert retry["messages"][:2] == [
        *first["messages"],
        {"role": "assistant", "content": invalid},
    ]
    feedback = retry["messages"][2]
    assert feedback["role"] == "user"
    assert "- workRelated: Input should be a valid boolean" in feedback["content"]
    assert (log.calls[0].attempts, log.calls[0].input_tokens) == (2, 200)


def test_rules_the_schema_cant_express_are_checked_too() -> None:
    too_many = Triage.model_construct(work_related=True, topics=("a", "b", "c", "d"))
    llm, fake, _ = make(reply(too_many), reply(WORK))
    llm.call("message_triage", PROMPT, Triage, variables=VARIABLES)
    feedback = fake.requests[1]["messages"][2]["content"]
    assert "- topics: Tuple should have at most 3 items" in feedback


def test_an_empty_reply_is_asked_again_as_it_was() -> None:
    llm, fake, _ = make(reply(""), reply(WORK))
    llm.call("message_triage", PROMPT, Triage, variables=VARIABLES)
    assert fake.requests[1]["messages"] == fake.requests[0]["messages"]


def test_output_invalid_twice_fails_without_quoting_the_reply() -> None:
    llm, _, log = make(reply('{"workRelated": "a private detail"}'), reply("not json"))
    with pytest.raises(LLMInvalidOutput, match="failed validation twice") as caught:
        llm.call("message_triage", PROMPT, Triage, variables=VARIABLES)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "private detail" not in str(caught.value)
    call = log.calls[0]
    assert (call.outcome, call.error, call.attempts) == ("invalid_output", "validation", 2)


def test_a_cut_off_reply_is_retried_with_twice_the_budget() -> None:
    llm, fake, _ = make(reply('{"workRel', stop_reason="max_tokens"), reply(WORK))
    result = llm.call("message_triage", PROMPT, Triage, variables=VARIABLES, max_tokens=1000)
    assert result.output == WORK
    assert [request["max_tokens"] for request in fake.requests] == [1000, 2000]
    assert fake.requests[1]["messages"] == fake.requests[0]["messages"]


def test_a_reply_cut_off_twice_fails() -> None:
    cut_off = reply("{", stop_reason="max_tokens")
    llm, _, log = make(cut_off, cut_off)
    with pytest.raises(LLMInvalidOutput, match="cut off at 2000 tokens"):
        llm.call("message_triage", PROMPT, Triage, variables=VARIABLES, max_tokens=1000)
    assert (log.calls[0].outcome, log.calls[0].error) == ("invalid_output", "max_tokens")


def test_a_refusal_is_raised_and_not_retried() -> None:
    llm, fake, log = make(refusal("cyber", "The request was declined."))
    with pytest.raises(LLMRefusal, match=r"declined the request \(cyber\)") as caught:
        llm.call("message_triage", PROMPT, Triage, variables=VARIABLES)
    assert caught.value.category == "cyber"
    assert caught.value.explanation == "The request was declined."
    assert len(fake.requests) == 1
    assert (log.calls[0].outcome, log.calls[0].error) == ("refusal", "cyber")


def test_a_fallback_reply_counts_both_attempts() -> None:
    llm, _, log = make(fallback_reply(WORK, declined_by="heavy-model", served_by="other-model"))
    result = llm.call("profile_synthesis", PROMPT, Triage, variables=VARIABLES)
    assert result.output == WORK
    call = log.calls[0]
    assert (call.model, call.served_by, call.fell_back) == ("heavy-model", "other-model", True)
    # The declined attempt read the same 100 input tokens before handing over.
    assert (call.input_tokens, call.output_tokens) == (200, 20)


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (api_error(OverloadedError, 529, "overloaded_error"), "OverloadedError (529)"),
        (api_error(RateLimitError, 429, "rate_limit_error"), "RateLimitError (429)"),
        # A stream that fails midway reports its error with the stream's status, 200.
        (api_error(APIStatusError, 200, "overloaded_error"), "APIStatusError (200)"),
        (APIConnectionError(request=httpx2.Request("POST", REQUEST_URL)), "APIConnectionError"),
    ],
)
def test_transient_errors_mean_the_api_is_unavailable(error: Exception, detail: str) -> None:
    llm, _, log = make(error)
    with pytest.raises(LLMUnavailable):
        llm.call("message_triage", PROMPT, Triage, variables=VARIABLES)
    assert (log.calls[0].outcome, log.calls[0].error) == ("unavailable", detail)


def test_other_api_errors_mean_the_request_was_rejected() -> None:
    llm, _, log = make(api_error(BadRequestError, 400, "invalid_request_error"))
    with pytest.raises(LLMRequestError, match=r"BadRequestError \(400\)"):
        llm.call("message_triage", PROMPT, Triage, variables=VARIABLES)
    call = log.calls[0]
    assert (call.outcome, call.error) == ("rejected", "BadRequestError (400)")
    assert call.request_id == "req_9"
    assert (call.attempts, call.input_tokens, call.cost_usd) == (1, 0, 0)


def test_the_call_log_holds_no_prompt_context_or_reply_text(tmp_path: Path) -> None:
    fake = FakeMessages(
        reply('{"workRelated": "maybe", "topics": ["first-reply-secret"]}'),
        reply(Triage(work_related=True, topics=("second-reply-secret",))),
    )
    log = JsonlCallLog(tmp_path / "calls.jsonl")
    llm = LLMClient(Settings(llm_model_light="light-model"), fake, log)
    llm.call(
        "message_triage",
        PROMPT,
        Triage,
        variables={"sender": "sender-secret", "message": "message-secret"},
        context="context-secret",
    )
    logged = log.path.read_text(encoding="utf-8")
    assert "secret" not in logged
    assert PROMPT.system not in logged


def test_from_settings_needs_no_network(tmp_path: Path) -> None:
    key = SecretStr("sk-test")
    settings = Settings(anthropic_api_key=key, llm_call_log=tmp_path / "calls.jsonl")
    assert isinstance(LLMClient.from_settings(settings), LLMClient)
    assert not settings.llm_call_log.exists()
