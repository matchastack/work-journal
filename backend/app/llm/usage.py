"""The record of each LLM call: task, model, prompt version, tokens, cost and latency.

Records hold no journal text, prompts or outputs (NFR-PRIV-2). The app saves them to the
database's `llm_calls` table (NFR-COST-1); the command line, which has no user, appends them to a
JSONL file.
"""

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.store import record_llm_call
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


class DatabaseCallLog:
    """Keeps one user's call records until `save` writes them to the `llm_calls` table.

    The client is synchronous and the database isn't, so a job collects the records of its calls
    and saves them in a `finally`, so that the calls of a job that fails are counted too.
    """

    def __init__(self, user_id: uuid.UUID) -> None:
        self.user_id = user_id
        self._pending: list[CallRecord] = []
        self._lock = threading.Lock()

    def record(self, call: CallRecord) -> None:
        with self._lock:
            self._pending.append(call)

    async def save(self, session: AsyncSession) -> int:
        """Write the records kept so far, and return how many there were."""
        with self._lock:
            calls, self._pending = self._pending, []
        for call in calls:
            await record_llm_call(session, self.user_id, call.model_dump(by_alias=False))
        return len(calls)


@dataclass
class MemoryCallLog:
    """Keeps records in memory, for tests."""

    calls: list[CallRecord] = field(default_factory=list[CallRecord])

    def record(self, call: CallRecord) -> None:
        self.calls.append(call)
