"""The monthly LLM cost on the Settings page (T-052, NFR-COST-2).

Each LLM call is logged in `llm_calls` with its tokens and an estimated cost (NFR-COST-1): its
tokens at its tier's price (`LLM_PRICE_HEAVY`, `LLM_PRICE_STANDARD` and `LLM_PRICE_LIGHT`) when
it was made (`app/llm/usage.py`). The view adds those up for one month, by task and by tier. A
call made while its tier had no price has no cost, so it's counted apart, and the total leaves
it out. The figures are estimates: only Anthropic's bill has the billed cost. Months are in UTC.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LlmCallRow
from app.llm.routing import TASK_TIERS
from app.schema.common import Model

TIER_ORDER = ("heavy", "standard", "light")
TASK_ORDER = tuple(TASK_TIERS)


class CostLine(Model):
    """The calls of a month, a task or a tier, with their tokens and estimated cost."""

    calls: int
    input_tokens: int
    """Input tokens not read from or written to the cache."""
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    """The estimated cost in US dollars of the calls that had a price."""
    unpriced_calls: int
    """Calls made while their tier had no price, which `cost_usd` leaves out."""


class TaskCost(CostLine):
    task: str
    """The task, such as `fact_extraction` (`app/llm/routing.py`)."""
    tier: str


class TierCost(CostLine):
    tier: str
    """`heavy`, `standard` or `light`."""


class LlmCosts(Model):
    """A month's LLM calls and their estimated cost, by task and by tier."""

    month: str
    """`YYYY-MM`, in UTC."""
    months: tuple[str, ...]
    """The months there's something to show for, newest first: those with calls, this month and
    `month`."""
    total: CostLine
    by_task: tuple[TaskCost, ...]
    by_tier: tuple[TierCost, ...]


@dataclass(frozen=True)
class Usage:
    """The month's calls of one task at one tier, as the database adds them up."""

    task: str
    tier: str
    calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: Decimal
    unpriced_calls: int


def month_of(moment: datetime) -> str:
    """The month, in UTC, a moment falls in, such as `2026-11`."""
    return f"{moment.astimezone(UTC):%Y-%m}"


def month_bounds(month: str) -> tuple[datetime, datetime]:
    """When a `YYYY-MM` month starts and when the next one does, in UTC."""
    year, number = (int(part) for part in month.split("-"))
    start = datetime(year, number, 1, tzinfo=UTC)
    end = datetime(year + number // 12, number % 12 + 1, 1, tzinfo=UTC)
    return start, end


def summarize(month: str, usages: Sequence[Usage], months: Iterable[str]) -> LlmCosts:
    """The cost view of `month` from its calls by task and tier."""

    def task_key(usage: Usage) -> tuple[int, int, str]:
        return (_rank(TIER_ORDER, usage.tier), _rank(TASK_ORDER, usage.task), usage.task)

    by_task = tuple(
        TaskCost(task=usage.task, tier=usage.tier, **_totals([usage]))
        for usage in sorted(usages, key=task_key)
    )
    tiers = sorted(
        {usage.tier for usage in usages}, key=lambda tier: (_rank(TIER_ORDER, tier), tier)
    )
    by_tier = tuple(
        TierCost(tier=tier, **_totals(usage for usage in usages if usage.tier == tier))
        for tier in tiers
    )
    return LlmCosts(
        month=month,
        months=tuple(sorted({*months, month}, reverse=True)),
        total=CostLine(**_totals(usages)),
        by_task=by_task,
        by_tier=by_tier,
    )


async def llm_costs(
    session: AsyncSession, user_id: uuid.UUID, month: str, *, now: datetime
) -> LlmCosts:
    """The user's LLM calls in `month` and their estimated cost."""
    start, end = month_bounds(month)
    call = LlmCallRow
    rows = await session.execute(
        select(
            call.task,
            call.tier,
            func.count().label("calls"),
            func.sum(call.input_tokens).label("input_tokens"),
            func.sum(call.output_tokens).label("output_tokens"),
            func.sum(call.cache_read_tokens).label("cache_read_tokens"),
            func.sum(call.cache_write_tokens).label("cache_write_tokens"),
            func.coalesce(func.sum(call.cost_usd), 0).label("cost_usd"),
            func.count().filter(call.cost_usd.is_(None)).label("unpriced_calls"),
        )
        .where(call.user_id == user_id, call.at >= start, call.at < end)
        .group_by(call.task, call.tier)
    )
    usages = [
        Usage(
            task=row.task,
            tier=row.tier,
            calls=row.calls,
            input_tokens=int(row.input_tokens),
            output_tokens=int(row.output_tokens),
            cache_read_tokens=int(row.cache_read_tokens),
            cache_write_tokens=int(row.cache_write_tokens),
            cost_usd=Decimal(row.cost_usd),
            unpriced_calls=row.unpriced_calls,
        )
        for row in rows
    ]
    called = await session.scalars(
        select(func.to_char(func.timezone("UTC", call.at), "YYYY-MM"))
        .where(call.user_id == user_id)
        .distinct()
    )
    return summarize(month, usages, [*called, month_of(now)])


def _totals(usages: Iterable[Usage]) -> dict[str, Any]:
    """The fields of a `CostLine` for some of the month's calls."""
    kept = list(usages)
    cost = sum((usage.cost_usd for usage in kept), Decimal(0))
    return {
        "calls": sum(usage.calls for usage in kept),
        "input_tokens": sum(usage.input_tokens for usage in kept),
        "output_tokens": sum(usage.output_tokens for usage in kept),
        "cache_read_tokens": sum(usage.cache_read_tokens for usage in kept),
        "cache_write_tokens": sum(usage.cache_write_tokens for usage in kept),
        "cost_usd": round(float(cost), 6),
        "unpriced_calls": sum(usage.unpriced_calls for usage in kept),
    }


def _rank(order: Sequence[str], value: str) -> int:
    """Where `value` comes in `order`; after everything in it when it isn't there."""
    return order.index(value) if value in order else len(order)
