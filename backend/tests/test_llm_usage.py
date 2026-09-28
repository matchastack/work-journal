import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.llm.usage import CallRecord, JsonlCallLog, MemoryCallLog, Tokens


def record(**overrides: object) -> CallRecord:
    values: dict[str, object] = {
        "at": datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        "task": "entry_summary",
        "tier": "light",
        "model": "light-model",
        "prompt": "entry_summary/v1",
        "outcome": "ok",
        "attempts": 1,
        "input_tokens": 1200,
        "output_tokens": 80,
        "latency_ms": 950,
    }
    return CallRecord.model_validate(values | overrides)


def test_tokens_add_up() -> None:
    total = Tokens(input=10, output=2, cache_read=100) + Tokens(input=5, output=1, cache_write=40)
    assert total == Tokens(input=15, output=3, cache_read=100, cache_write=40)


def test_cost_needs_a_price() -> None:
    assert Tokens(input=1000, output=100).cost_usd(None) is None


def test_cost_counts_cache_writes_and_reads_at_their_rates() -> None:
    tokens = Tokens(input=1_000_000, output=1_000_000, cache_read=1_000_000, cache_write=1_000_000)
    # $2 input + $10 output + $2 x 0.1 cache read + $2 x 1.25 cache write
    assert tokens.cost_usd((2, 10)) == pytest.approx(2 + 10 + 0.2 + 2.5)


def test_jsonl_log_appends_one_line_per_call(tmp_path: Path) -> None:
    log = JsonlCallLog(tmp_path / "logs" / "calls.jsonl")
    log.record(record())
    log.record(record(outcome="refusal", error="cyber"))
    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["outcome"] for line in lines] == ["ok", "refusal"]
    assert json.loads(lines[0])["inputTokens"] == 1200


def test_memory_log_keeps_records() -> None:
    log = MemoryCallLog()
    log.record(record())
    assert [call.task for call in log.calls] == ["entry_summary"]
