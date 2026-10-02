"""The one way to call Claude (requirements §10, NFR-COST-1, NFR-REL-2).

`LLMClient.call` takes a task, a versioned prompt and the Pydantic model the reply must match:

- The task's tier picks the model ID from the settings; no model ID is hardcoded.
- The prompt's instructions, and any stable context such as the profile, go in system blocks
  with cache breakpoints. The rendered user template is the only text that changes per call.
- The reply is constrained to the output model's JSON schema, then validated with Pydantic. A
  reply cut off at the token budget is retried once with twice the budget; a reply that fails
  validation is retried once with the errors as feedback. A second failure raises
  `LLMInvalidOutput`.
- A refusal raises `LLMRefusal`. On the heavy tier the API first reruns a refused request on its
  recommended fallback model, unless `LLM_HEAVY_FALLBACK` is off.
- The SDK retries connection errors and 408, 409, 429 and 5xx responses. An error that persists
  raises `LLMUnavailable`; any other API error raises `LLMRequestError`.
- Every call that reaches the API is recorded in the call log (`usage.py`) with its task, models,
  prompt version, tokens, cost and latency, never any text (NFR-PRIV-2).

Replies are streamed, so long outputs don't hit the SDK's limit on non-streaming requests.
"""

import time
from collections.abc import Iterable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar, Protocol

from anthropic import (
    Anthropic,
    AnthropicError,
    APIConnectionError,
    APIStatusError,
    Omit,
    omit,
    transform_schema,
)
from anthropic.types import AnthropicBetaParam
from anthropic.types.beta import (
    BetaFallbackMessageIterationUsage,
    BetaFallbacksParam,
    BetaMessage,
    BetaMessageIterationUsage,
    BetaMessageParam,
    BetaOutputConfigParam,
    BetaTextBlockParam,
    BetaUsage,
)
from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings
from app.llm.prompts import Prompt
from app.llm.routing import TASK_TIERS, Task, Tier
from app.llm.usage import CallLog, CallRecord, JsonlCallLog, Outcome, Tokens

DEFAULT_MAX_TOKENS = 16_000
"""Output budget per attempt, thinking included. A reply cut off at the budget is retried with
twice the budget, so a caller's budget must stay within half the model's output limit."""
MAX_ATTEMPTS = 2
"""The first attempt and one retry (requirements §10)."""
SDK_MAX_RETRIES = 3
"""How often the SDK retries a connection error or a 408, 409, 429 or 5xx response."""
FALLBACK_BETA: AnthropicBetaParam = "server-side-fallback-2026-07-01"
"""The beta header that `fallbacks="default"` needs."""
MAX_FEEDBACK_ERRORS = 20

_TRANSIENT_STATUS = frozenset({408, 409, 429})
_TRANSIENT_TYPES = frozenset({"rate_limit_error", "timeout_error", "overloaded_error", "api_error"})
"""Error types that mean "try later". A stream that fails midway reports one with status 200."""


class LLMError(Exception):
    """Base for the client's errors. Messages never contain prompt or reply text."""


class LLMConfigError(LLMError):
    """A setting the call needs, such as the tier's model ID, is missing. Nothing was sent."""


class LLMCallError(LLMError):
    """The API was called but gave no usable reply. The call log has a record of it."""

    outcome: ClassVar[Outcome]

    def __init__(self, message: str, detail: str | None) -> None:
        super().__init__(message)
        self.detail = detail
        """What the call log records: an error class or refusal category, never content."""


class LLMRefusal(LLMCallError):
    """Claude declined the request, after any fallback. Show it to the owner (NFR-REL-2)."""

    outcome = "refusal"

    def __init__(self, category: str | None, explanation: str | None) -> None:
        reason = f" ({category})" if category else ""
        super().__init__(f"Claude declined the request{reason}", detail=category)
        self.category = category
        self.explanation = explanation


class LLMInvalidOutput(LLMCallError):
    """The reply was cut off or failed validation on both attempts."""

    outcome = "invalid_output"


class LLMUnavailable(LLMCallError):
    """The API was unreachable or overloaded after the SDK's retries. Try again later."""

    outcome = "unavailable"


class LLMRequestError(LLMCallError):
    """The API rejected the request, e.g. an unknown model or a bad key. Retrying won't help."""

    outcome = "rejected"


class MessageStream(Protocol):
    @property
    def request_id(self) -> str | None: ...

    def get_final_message(self) -> BetaMessage: ...


class Messages(Protocol):
    """The part of the SDK's `client.beta.messages` this client uses. `FakeMessages` has it too."""

    def stream(
        self,
        *,
        model: str,
        max_tokens: int,
        system: Iterable[BetaTextBlockParam],
        messages: Iterable[BetaMessageParam],
        output_config: BetaOutputConfigParam,
        betas: list[AnthropicBetaParam] | Omit = ...,
        fallbacks: BetaFallbacksParam | Omit = ...,
    ) -> AbstractContextManager[MessageStream]: ...


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    output: T
    call: CallRecord


@dataclass
class _Call:
    """One `LLMClient.call`: the request as it stands, and what the replies have used so far."""

    model: str
    max_tokens: int
    system: list[BetaTextBlockParam]
    messages: list[BetaMessageParam]
    schema: dict[str, object]
    fallback: bool
    attempts: int = 0
    tokens: Tokens = field(default_factory=Tokens)
    served_by: str | None = None
    fell_back: bool = False
    request_id: str | None = None


class LLMClient:
    def __init__(self, settings: Settings, messages: Messages, log: CallLog) -> None:
        self._settings = settings
        self._messages = messages
        self._log = log

    @classmethod
    def from_settings(
        cls, settings: Settings | None = None, log: CallLog | None = None
    ) -> "LLMClient":
        """A client for the real API. It logs calls to `log`, such as a user's
        `DatabaseCallLog`, or else to the JSONL file in the settings."""
        settings = settings or get_settings()
        key = settings.anthropic_api_key
        api_key = key.get_secret_value() if key else None
        sdk = Anthropic(api_key=api_key, max_retries=SDK_MAX_RETRIES)
        return cls(settings, sdk.beta.messages, log or JsonlCallLog(settings.llm_call_log))

    def call[T: BaseModel](
        self,
        task: Task,
        prompt: Prompt,
        output: type[T],
        *,
        variables: Mapping[str, str] | None = None,
        context: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMResult[T]:
        """Run `prompt` for `task` and return the reply, parsed as `output`.

        `variables` fill the prompt's user template; per-call text such as a journal entry goes
        there. `context` is stable text, such as the profile as JSON, that is cached with the
        instructions so that calls sharing it pay less for it.
        """
        tier = TASK_TIERS[task]
        system: list[BetaTextBlockParam] = [_cached(prompt.system)]
        if context:
            system.append(_cached(context))
        call = _Call(
            model=self._model(tier),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt.render(variables or {})}],
            schema=transform_schema(output),
            fallback=tier == "heavy" and self._settings.llm_heavy_fallback,
        )
        at = datetime.now(UTC)
        started = time.perf_counter()
        try:
            value = self._attempt(call, output)
        except LLMCallError as error:
            self._record(call, task, tier, prompt, at, started, error.outcome, error.detail)
            raise
        record = self._record(call, task, tier, prompt, at, started, "ok", None)
        return LLMResult(output=value, call=record)

    def _model(self, tier: Tier) -> str:
        models = {
            "heavy": self._settings.llm_model_heavy,
            "standard": self._settings.llm_model_standard,
            "light": self._settings.llm_model_light,
        }
        model = models[tier]
        if not model:
            raise LLMConfigError(f"set LLM_MODEL_{tier.upper()} to run {tier}-tier tasks")
        return model

    def _price(self, tier: Tier) -> tuple[float, float] | None:
        prices = {
            "heavy": self._settings.llm_price_heavy,
            "standard": self._settings.llm_price_standard,
            "light": self._settings.llm_price_light,
        }
        return prices[tier]

    def _attempt[T: BaseModel](self, call: _Call, output: type[T]) -> T:
        while True:
            message = self._send(call)
            if message.stop_reason == "refusal":
                details = message.stop_details
                raise LLMRefusal(
                    category=details.category if details else None,
                    explanation=details.explanation if details else None,
                )
            text = "".join(block.text for block in message.content if block.type == "text")
            last_attempt = call.attempts >= MAX_ATTEMPTS
            if message.stop_reason == "max_tokens":
                if last_attempt:
                    raise LLMInvalidOutput(
                        f"the reply was cut off at {call.max_tokens} tokens", detail="max_tokens"
                    )
                call.max_tokens *= 2
                continue
            try:
                return output.model_validate_json(text)
            except ValidationError as error:
                invalid = error
            # Raised outside the `except` block: a ValidationError quotes the reply, so it must
            # not travel with the error into logs or error reports.
            if last_attempt:
                raise LLMInvalidOutput(
                    f"the reply failed validation twice ({invalid.error_count()} errors)",
                    detail="validation",
                )
            if text.strip():
                call.messages.append({"role": "assistant", "content": text})
                call.messages.append({"role": "user", "content": _feedback(invalid)})

    def _send(self, call: _Call) -> BetaMessage:
        call.attempts += 1
        try:
            with self._messages.stream(
                model=call.model,
                max_tokens=call.max_tokens,
                system=call.system,
                messages=call.messages,
                output_config={"format": {"type": "json_schema", "schema": call.schema}},
                betas=[FALLBACK_BETA] if call.fallback else omit,
                fallbacks="default" if call.fallback else omit,
            ) as stream:
                message = stream.get_final_message()
                call.request_id = stream.request_id
        except APIConnectionError as error:
            raise LLMUnavailable(
                "couldn't reach the Claude API", detail=type(error).__name__
            ) from error
        except APIStatusError as error:
            call.request_id = error.request_id
            detail = f"{type(error).__name__} ({error.status_code})"
            if _transient(error):
                raise LLMUnavailable(f"the Claude API is unavailable: {detail}", detail) from error
            raise LLMRequestError(f"the Claude API refused the call: {detail}", detail) from error
        except AnthropicError as error:
            detail = type(error).__name__
            raise LLMRequestError(f"the request couldn't be sent: {detail}", detail) from error
        call.tokens += _tokens(message.usage)
        call.served_by = message.model
        call.fell_back = call.fell_back or _fell_back(message.usage)
        return message

    def _record(
        self,
        call: _Call,
        task: Task,
        tier: Tier,
        prompt: Prompt,
        at: datetime,
        started: float,
        outcome: Outcome,
        error: str | None,
    ) -> CallRecord:
        record = CallRecord(
            at=at,
            task=task,
            tier=tier,
            model=call.model,
            served_by=call.served_by,
            fell_back=call.fell_back,
            prompt=prompt.id,
            outcome=outcome,
            attempts=call.attempts,
            input_tokens=call.tokens.input,
            output_tokens=call.tokens.output,
            cache_read_tokens=call.tokens.cache_read,
            cache_write_tokens=call.tokens.cache_write,
            cost_usd=call.tokens.cost_usd(self._price(tier)),
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_id=call.request_id,
            error=error,
        )
        self._log.record(record)
        return record


def _cached(text: str) -> BetaTextBlockParam:
    """A system block ending a cached prefix. Prefixes below the model's minimum aren't cached."""
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def _transient(error: APIStatusError) -> bool:
    return (
        error.status_code in _TRANSIENT_STATUS
        or error.status_code >= 500
        or error.type in _TRANSIENT_TYPES
    )


def _tokens(usage: BetaUsage) -> Tokens:
    """The tokens one reply used. After a fallback, the declined attempt counts too."""
    if _fell_back(usage):
        return sum(
            (
                Tokens(
                    input=step.input_tokens,
                    output=step.output_tokens,
                    cache_read=step.cache_read_input_tokens,
                    cache_write=step.cache_creation_input_tokens,
                )
                for step in usage.iterations or ()
                if isinstance(step, BetaMessageIterationUsage | BetaFallbackMessageIterationUsage)
            ),
            Tokens(),
        )
    return Tokens(
        input=usage.input_tokens,
        output=usage.output_tokens,
        cache_read=usage.cache_read_input_tokens or 0,
        cache_write=usage.cache_creation_input_tokens or 0,
    )


def _fell_back(usage: BetaUsage) -> bool:
    steps = usage.iterations or ()
    return any(isinstance(step, BetaFallbackMessageIterationUsage) for step in steps)


def _feedback(error: ValidationError) -> str:
    """Ask for a corrected reply, listing each failed field and rule."""
    problems = error.errors(include_url=False, include_input=False, include_context=False)
    lines = [
        f"- {'.'.join(str(part) for part in problem['loc']) or '(the whole reply)'}: "
        f"{problem['msg']}"
        for problem in problems[:MAX_FEEDBACK_ERRORS]
    ]
    return (
        "Your reply doesn't match the required schema:\n"
        + "\n".join(lines)
        + "\nReply again with the corrected JSON only."
    )
