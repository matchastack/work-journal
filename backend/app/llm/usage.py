"""The record of each LLM call: task, model, prompt version, tokens, cost and latency.

Records hold no journal text, prompts or outputs (NFR-PRIV-2). They go to a JSONL file until
the database's `llm_calls` table exists (NFR-COST-1).
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from app.llm.routing import Task, Tier
from app.schema.common import Model

Outcome = Literal["ok", "refusal", "invalid_output", "unavailable", "rejected"]

CACHE_WRITE_RATE = 1.25
"""Writing a 5-minute cache entry costs 1.25 times the input price."""
CACHE_READ_RATE = 0.1
"""Reading from the cache costs a tenth of the input price."""


@dataclass(frozen=True)
class Tokens:
    input: int = 0
    """Input tokens not read from or written to the cache."""
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def __add__(self, other: "Tokens") -> "Tokens":
        return Tokens(
            input=self.input + other.input,
            output=self.output + other.output,
            cache_read=self.cache_read + other.cache_read,
            cache_write=self.cache_write + other.cache_write,
        )

    def cost_usd(self, price: tuple[float, float] | None) -> float | None:
        """The cost at `price` (USD per million input and output tokens), if there's a price."""
        if price is None:
            return None
        per_input, per_output = price
        input_cost = per_input * (
            self.input + self.cache_write * CACHE_WRITE_RATE + self.cache_read * CACHE_READ_RATE
        )
        return round((input_cost + per_output * self.output) / 1_000_000, 6)


class CallRecord(Model):
    at: datetime
    task: Task
    tier: Tier
    model: str
    """The model the tier asked for."""
    served_by: str | None = None
    """The model that produced the last reply, as the API names it."""
    fell_back: bool = False
    """The requested model declined, and the API's fallback model answered instead."""
    prompt: str
    """The prompt and version, e.g. `fact_extraction/v2`."""
    outcome: Outcome
    attempts: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None
    """Estimated at the tier's price; a fallback model may bill at its own rates."""
    latency_ms: int
    request_id: str | None = None
    error: str | None = None
    """What went wrong, e.g. a refusal's category or an error class; never any content."""


class CallLog(Protocol):
    def record(self, call: CallRecord) -> None: ...


class JsonlCallLog:
    """Appends one JSON line per call to a file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, call: CallRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(call.model_dump(mode="json")) + "\n")


@dataclass
class MemoryCallLog:
    """Keeps records in memory, for tests."""

    calls: list[CallRecord] = field(default_factory=list[CallRecord])

    def record(self, call: CallRecord) -> None:
        self.calls.append(call)
