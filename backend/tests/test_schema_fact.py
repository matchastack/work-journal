import pytest
from pydantic import ValidationError

from app.schema.fact import ChangeValue, Fact, Metric, RangeValue, SingleValue


def test_metric_values_are_told_apart_by_kind() -> None:
    metrics = [
        Metric.model_validate({"subject": "accuracy", "value": {"kind": "single", "value": 93}}),
        Metric.model_validate(
            {"subject": "export duration", "value": {"kind": "change", "before": 50, "after": 12}}
        ),
        Metric.model_validate(
            {"subject": "time saved", "value": {"kind": "range", "low": 2, "high": 3}}
        ),
    ]
    assert isinstance(metrics[0].value, SingleValue)
    assert isinstance(metrics[1].value, ChangeValue)
    assert isinstance(metrics[2].value, RangeValue)


def test_metric_defaults_to_exact_without_unit() -> None:
    metric = Metric(subject="papers reviewed", value=SingleValue(value=200))
    assert metric.qualifier == "exact"
    assert metric.unit is None


def test_metric_keeps_qualifier_and_unit() -> None:
    metric = Metric.model_validate(
        {
            "subject": "papers reviewed",
            "value": {"kind": "single", "value": 200},
            "unit": "papers",
            "qualifier": "at_least",
        }
    )
    assert metric.qualifier == "at_least"
    assert metric.unit == "papers"


def test_unknown_qualifier_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Metric.model_validate(
            {"subject": "x", "value": {"kind": "single", "value": 1}, "qualifier": "roughly"}
        )


def test_range_low_must_not_exceed_high() -> None:
    with pytest.raises(ValidationError, match="low must not be above high"):
        RangeValue(low=5, high=2)


def test_metric_value_needs_a_kind() -> None:
    with pytest.raises(ValidationError):
        Metric.model_validate({"subject": "x", "value": {"value": 1}})


def test_fact_round_trips_with_links_and_metrics() -> None:
    fact = Fact.model_validate(
        {
            "id": "fact_nw_export",
            "statement": "Batched database writes in the nightly export job",
            "kind": "accomplishment",
            "metrics": [
                {
                    "subject": "export duration",
                    "value": {"kind": "change", "before": 50, "after": 12},
                    "unit": "minutes",
                }
            ],
            "ownership": "owned",
            "tools": ["Python", "PostgreSQL"],
            "date": "2026-03",
            "roleId": "northwind",
            "entryId": "entry-42",
        }
    )
    assert fact.role_id == "northwind"
    assert Fact.model_validate_json(fact.model_dump_json()) == fact


def test_fact_defaults() -> None:
    fact = Fact(id="f1", statement="Learned the basics of Kafka")
    assert fact.kind == "accomplishment"
    assert fact.ownership is None
    assert fact.origin == "journal"


def test_fact_rejects_unknown_ownership() -> None:
    with pytest.raises(ValidationError):
        Fact.model_validate({"id": "f1", "statement": "x", "ownership": "spearheaded"})
