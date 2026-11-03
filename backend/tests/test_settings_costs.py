"""The monthly LLM cost view's sums (T-052, NFR-COST-2)."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from app.settings.costs import Usage, month_bounds, month_of, summarize


def usage(task: str, tier: str, cost: str, *, calls: int = 1, unpriced: int = 0) -> Usage:
    return Usage(
        task=task,
        tier=tier,
        calls=calls,
        input_tokens=1000 * calls,
        output_tokens=100 * calls,
        cache_read_tokens=10 * calls,
        cache_write_tokens=1 * calls,
        cost_usd=Decimal(cost),
        unpriced_calls=unpriced,
    )


def test_a_month_runs_from_its_first_day_to_the_next_months_in_utc() -> None:
    assert month_bounds("2026-10") == (
        datetime(2026, 10, 1, tzinfo=UTC),
        datetime(2026, 11, 1, tzinfo=UTC),
    )
    assert month_bounds("2026-12") == (
        datetime(2026, 12, 1, tzinfo=UTC),
        datetime(2027, 1, 1, tzinfo=UTC),
    )


def test_the_month_of_a_moment_is_the_one_in_utc() -> None:
    singapore = timezone(timedelta(hours=8))
    assert month_of(datetime(2026, 11, 1, 5, 0, tzinfo=singapore)) == "2026-10"
    assert month_of(datetime(2026, 11, 1, 8, 0, tzinfo=singapore)) == "2026-11"


def test_the_month_adds_up_by_task_and_by_tier() -> None:
    costs = summarize(
        "2026-10",
        [
            usage("entry_summary", "light", "0.0002", calls=2),
            usage("fact_extraction", "standard", "0.0054", calls=3, unpriced=1),
            usage("profile_synthesis", "heavy", "0.25"),
            usage("claim_verification", "standard", "0.001"),
        ],
        ["2026-09", "2026-11", "2026-10", "2026-09"],
    )
    assert costs.month == "2026-10"
    assert costs.months == ("2026-11", "2026-10", "2026-09")
    assert [(line.task, line.tier) for line in costs.by_task] == [
        ("profile_synthesis", "heavy"),
        ("fact_extraction", "standard"),
        ("claim_verification", "standard"),
        ("entry_summary", "light"),
    ]
    standard = costs.by_tier[1]
    assert [tier.tier for tier in costs.by_tier] == ["heavy", "standard", "light"]
    assert (standard.calls, standard.unpriced_calls, standard.cost_usd) == (4, 1, 0.0064)
    assert standard.input_tokens == 4000
    assert costs.total.calls == 7
    assert costs.total.cost_usd == 0.2566
    assert costs.total.unpriced_calls == 1
    assert (costs.total.output_tokens, costs.total.cache_read_tokens) == (700, 70)
    assert costs.total.cache_write_tokens == 7


def test_a_task_or_tier_the_app_no_longer_has_comes_last() -> None:
    costs = summarize(
        "2026-10",
        [usage("old_task", "standard", "0.1"), usage("bullet_writing", "medium", "0.2")],
        [],
    )
    assert [line.task for line in costs.by_task] == ["old_task", "bullet_writing"]
    assert [tier.tier for tier in costs.by_tier] == ["standard", "medium"]


def test_a_month_without_calls_is_empty() -> None:
    costs = summarize("2026-08", [], ["2026-11"])
    assert costs.months == ("2026-11", "2026-08")
    assert costs.by_task == costs.by_tier == ()
    assert costs.total.model_dump() == {
        "calls": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "costUsd": 0.0,
        "unpricedCalls": 0,
    }
