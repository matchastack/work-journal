"""Number checker: every number in generated text must be backed by the facts it cites.

Deterministic, no LLM (FR-FID-1..3, FR-EXT-2). Quantities are found in the text and each must be
supported by a fact metric: the same numbers, a compatible unit, and a qualifier no stronger than
the fact's. Numbers are compared as intervals: "300+" is [300, inf), "about 300" is 300 +/- 10%,
and text is supported only if the fact's interval lies inside the text's. Figures derived by code
from a before/after metric (percentage change, ratio, difference) are accepted with their formula.
Metrics the text leaves out are warnings.
"""

import math
from collections.abc import Sequence
from typing import Literal

from app.schema.common import Model
from app.schema.fact import ChangeValue, Metric, Qualifier, RangeValue, SingleValue
from app.validate.quantities import (
    Interval,
    Quantity,
    find_quantities,
    normalize_unit,
    units_compatible,
)

APPROX_TOLERANCE = 0.10
"""How far "about N" may stretch either side of N."""

FindingCode = Literal[
    "unsupported_number", "unit_changed", "qualifier_stronger", "qualifier_weakened",
    "metric_dropped",
]  # fmt: skip


class NumberFinding(Model):
    severity: Literal["error", "warning"]
    code: FindingCode
    message: str


class Derivation(Model):
    """A figure in the text that code computed from a fact metric, and how."""

    quantity: str
    formula: str


class NumberCheck(Model):
    findings: tuple[NumberFinding, ...] = ()
    derivations: tuple[Derivation, ...] = ()

    @property
    def ok(self) -> bool:
        """True when there are no errors (warnings are allowed)."""
        return not any(finding.severity == "error" for finding in self.findings)


def check_numbers(
    text: str, metrics: Sequence[Metric], allowed_terms: Sequence[str] = ()
) -> NumberCheck:
    """Check every number in `text` against `metrics`, the metrics of the facts it cites."""
    findings: list[NumberFinding] = []
    derivations: list[Derivation] = []
    used: set[int] = set()
    for quantity in find_quantities(text, allowed_terms):
        outcome = _check_quantity(quantity, metrics)
        if isinstance(outcome, Derivation):
            derivations.append(outcome)
            used.update(_derivation_sources(quantity, metrics))
            continue
        finding, index = outcome
        if finding is not None:
            findings.append(finding)
        if index is not None:
            used.add(index)
    for index, metric in enumerate(metrics):
        if index not in used:
            findings.append(
                NumberFinding(
                    severity="warning",
                    code="metric_dropped",
                    message=f"the fact's metric {describe_metric(metric)} isn't mentioned",
                )
            )
    return NumberCheck(findings=tuple(findings), derivations=tuple(derivations))


def describe_metric(metric: Metric) -> str:
    """E.g. `export duration: 50 -> 12 minute` or `tickets: at least 300`."""
    value = metric.value
    if isinstance(value, SingleValue):
        numbers = _fmt(value.value)
    elif isinstance(value, ChangeValue):
        numbers = f"{_fmt(value.before)} -> {_fmt(value.after)}"
    else:
        numbers = f"{_fmt(value.low)}-{_fmt(value.high)}"
    qualifier = "" if metric.qualifier == "exact" else metric.qualifier.replace("_", " ") + " "
    unit = f" {metric.unit}" if metric.unit else ""
    return f"'{metric.subject}: {qualifier}{numbers}{unit}'"


def _check_quantity(
    quantity: Quantity, metrics: Sequence[Metric]
) -> tuple[NumberFinding | None, int | None] | Derivation:
    same_numbers: list[tuple[int, Metric]] = []
    for index, metric in enumerate(metrics):
        pairs = _component_pairs(quantity, metric)
        if not pairs:
            continue
        units_ok = units_compatible(quantity.unit, normalize_unit(metric.unit))
        if units_ok and any(all(_contains(t, f) for t, f in pair) for pair in pairs):
            if quantity.qualifier == metric.qualifier and _same_numbers(quantity, metric):
                return None, index
            return _finding("warning", "qualifier_weakened", quantity, metric), index
        if any(all(_close(t, f) for t, f in pair) for pair in _raw_pairs(quantity, metric)):
            same_numbers.append((index, metric))
    for index, metric in same_numbers:
        if not units_compatible(quantity.unit, normalize_unit(metric.unit)):
            return _finding("error", "unit_changed", quantity, metric), index
        return _finding("error", "qualifier_stronger", quantity, metric), index
    if derivation := _derive(quantity, metrics):
        return derivation
    message = f"'{quantity.text}' isn't in the cited facts"
    return NumberFinding(severity="error", code="unsupported_number", message=message), None


def _finding(
    severity: Literal["error", "warning"], code: FindingCode, quantity: Quantity, metric: Metric
) -> NumberFinding:
    what = {
        "unit_changed": "uses a different unit from",
        "qualifier_stronger": "claims more than",
        "qualifier_weakened": "is less precise than",
    }[code]
    return NumberFinding(
        severity=severity,
        code=code,
        message=f"'{quantity.text}' {what} the fact {describe_metric(metric)}",
    )


def _interval(value: float, qualifier: Qualifier) -> Interval:
    # Open bounds ("over", "under") are nudged by more than `_contains`' float tolerance, so that
    # "over 200" never contains exactly 200.
    epsilon = 1e-6 * max(1.0, abs(value))
    match qualifier:
        case "exact":
            return (value, value)
        case "approximately":
            spread = abs(value) * APPROX_TOLERANCE
            return (value - spread, value + spread)
        case "at_least":
            return (value, math.inf)
        case "more_than":
            return (value + epsilon, math.inf)
        case "at_most":
            return (-math.inf, value)
        case "less_than":
            return (-math.inf, value - epsilon)


def _contains(outer: Interval, inner: Interval) -> bool:
    tolerance = 1e-9 * max(1.0, abs(inner[0]) if math.isfinite(inner[0]) else 1.0)
    return outer[0] <= inner[0] + tolerance and inner[1] <= outer[1] + tolerance


def _close(a: Interval, b: Interval) -> bool:
    return math.isclose(a[0], b[0], rel_tol=1e-9) and math.isclose(a[1], b[1], rel_tol=1e-9)


def _text_intervals(quantity: Quantity) -> list[Interval]:
    """The quantity's components as intervals; a range is one interval."""
    if quantity.kind == "range":
        low = _interval(quantity.values[0], quantity.qualifier)[0]
        high = _interval(quantity.values[-1], quantity.qualifier)[1]
        return [(low, high)]
    return [_interval(value, quantity.qualifier) for value in quantity.values]


def _fact_shapes(metric: Metric) -> dict[str, list[Interval]]:
    value, qualifier = metric.value, metric.qualifier
    if isinstance(value, SingleValue):
        return {"single": [_interval(value.value, qualifier)]}
    if isinstance(value, RangeValue):
        interval = (_interval(value.low, qualifier)[0], _interval(value.high, qualifier)[1])
        return {"range": [interval], "pair_range": [
            _interval(value.low, qualifier), _interval(value.high, qualifier)
        ]}  # fmt: skip
    return {"change": [_interval(value.before, qualifier), _interval(value.after, qualifier)]}


def _component_pairs(quantity: Quantity, metric: Metric) -> list[list[tuple[Interval, Interval]]]:
    """Ways to line up the quantity's components with the metric's, as (text, fact) intervals."""
    text = _text_intervals(quantity)
    fact = _fact_shapes(metric)
    options: list[list[Interval]] = []
    match quantity.kind:
        case "single":
            options = [[interval] for shape in fact.values() for interval in shape[:2]]
            if "range" in fact:
                options = [fact["range"]]
        case "range":
            options = [fact[shape] for shape in ("range", "single") if shape in fact]
        case "change":
            options = [fact["change"]] if "change" in fact else []
        case "pair":
            options = [fact[shape] for shape in ("change", "pair_range") if shape in fact]
    return [list(zip(text, option, strict=True)) for option in options if len(option) == len(text)]


def _raw_pairs(quantity: Quantity, metric: Metric) -> list[list[tuple[Interval, Interval]]]:
    """Like `_component_pairs`, but comparing bare numbers with qualifiers ignored."""
    bare_text = Quantity(quantity.text, quantity.kind, quantity.values, quantity.unit, "exact")
    bare_metric = metric.model_copy(update={"qualifier": "exact"})
    return _component_pairs(bare_text, bare_metric)


def _same_numbers(quantity: Quantity, metric: Metric) -> bool:
    return any(all(_close(t, f) for t, f in pair) for pair in _raw_pairs(quantity, metric))


def _derive(quantity: Quantity, metrics: Sequence[Metric]) -> Derivation | None:
    """Accept a percentage change, ratio or difference that code computes from a change metric."""
    if quantity.kind != "single":
        return None
    shown = quantity.values[0]
    rounding = 0.5 * 10**-quantity.decimals
    if quantity.qualifier == "exact":
        text_interval = (shown - rounding, shown + rounding)
    else:
        text_interval = _interval(shown, quantity.qualifier)
    for metric in metrics:
        if not isinstance(metric.value, ChangeValue):
            continue
        before, after = metric.value.before, metric.value.after
        unit = normalize_unit(metric.unit) or ""
        candidates: list[tuple[str | None, float, str]] = []
        if before:
            percent = abs(after - before) / abs(before) * 100
            candidates.append(("%", percent, f"|{_fmt(after)} - {_fmt(before)}| / {_fmt(before)}"))
        if min(before, after) > 0:
            ratio = max(before, after) / min(before, after)
            candidates.append(
                ("x", ratio, f"{_fmt(max(before, after))} / {_fmt(min(before, after))}")
            )
        candidates.append((unit or None, abs(after - before), f"|{_fmt(after)} - {_fmt(before)}|"))
        for derived_unit, value, formula in candidates:
            if not units_compatible(quantity.unit, derived_unit):
                continue
            if quantity.unit is None and derived_unit in {"%", "x"}:
                continue
            if _contains(text_interval, (value, value)):
                return Derivation(
                    quantity=quantity.text, formula=f"{formula} = {_fmt(round(value, 4))}"
                )
    return None


def _derivation_sources(quantity: Quantity, metrics: Sequence[Metric]) -> set[int]:
    """The change metrics a derived figure may have come from (so they aren't reported dropped)."""
    return {
        index
        for index, metric in enumerate(metrics)
        if isinstance(metric.value, ChangeValue) and _derive(quantity, [metric]) is not None
    }


def _fmt(value: float) -> str:
    return f"{value:g}" if abs(value) < 1e15 else str(value)
