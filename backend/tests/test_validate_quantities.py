import pytest

from app.validate.quantities import Quantity, extract_metrics, find_quantities, normalize_unit


def only(text: str, allowed_terms: tuple[str, ...] = ()) -> Quantity:
    quantities = find_quantities(text, allowed_terms)
    assert len(quantities) == 1, quantities
    return quantities[0]


@pytest.mark.parametrize(
    ("text", "values", "unit", "qualifier", "kind"),
    [
        ("Triaged 300+ tickets", (300,), "ticket", "at_least", "single"),
        ("Triaged about 300 tickets", (300,), "ticket", "approximately", "single"),
        ("Triaged ~300 tickets", (300,), "ticket", "approximately", "single"),
        ("served over 200 users", (200,), "user", "more_than", "single"),
        ("served more than 200 users", (200,), "user", "more_than", "single"),
        ("at least 10 teams", (10,), "team", "at_least", "single"),
        ("up to 3 hours", (3,), "hour", "at_most", "single"),
        ("in under 5 minutes", (5,), "minute", "less_than", "single"),
        ("200 or more orders", (200,), "order", "at_least", "single"),
        ("improving recall by 15%", (15,), "%", "exact", "single"),
        ("improving recall by 15\\%", (15,), "%", "exact", "single"),
        ("accuracy of 88 percent", (88,), "%", "exact", "single"),
        ("raising throughput 5x", (5,), "x", "exact", "single"),
        ("4 times faster", (4,), "x", "exact", "single"),
        ("saving $1.2M a year", (1_200_000,), "$", "exact", "single"),
        ("a S$50k budget", (50_000,), "sgd", "exact", "single"),
        ("2,500+ sample photos", (2500,), "sample", "at_least", "single"),
        ("across two regions", (2,), "region", "exact", "single"),
        ("a 9-person team", (9,), "person", "exact", "single"),
        ("1,500 to 7,500 requests", (1500, 7500), "request", "exact", "pair"),
        ("from 12% to 4.5%", (12, 4.5), "%", "exact", "change"),
        ("from 50 to 12 minutes", (50, 12), "minute", "exact", "change"),
        ("3\u20135 days a month", (3, 5), "day", "exact", "range"),
        ("3--5 days a month", (3, 5), "day", "exact", "range"),
        ("3-5 days a month", (3, 5), "day", "exact", "range"),
        ("between 3 and 5 days", (3, 5), "day", "exact", "range"),
        ("hundreds of active users", (200, 999), "active", "exact", "range"),
    ],
)
def test_finds_quantities(
    text: str, values: tuple[float, ...], unit: str, qualifier: str, kind: str
) -> None:
    quantity = only(text)
    assert quantity.values == values
    assert quantity.unit == unit
    assert quantity.qualifier == qualifier
    assert quantity.kind == kind


def test_quantity_text_covers_qualifier_and_unit() -> None:
    assert only("Triaged about 300 tickets").text == "about 300 tickets"
    assert only("cut it from 50 to 12 minutes").text == "50 to 12 minutes"


def test_several_quantities_in_one_bullet() -> None:
    text = "raising throughput 5x (1,500 to 7,500 requests) in 3 weeks"
    assert [(q.values, q.unit) for q in find_quantities(text)] == [
        ((5,), "x"),
        ((1500, 7500), "request"),
        ((3,), "week"),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Implemented OAuth2 login",
        "Evaluated GPT-4 outputs",
        "Served over HTTP/2",
        "Upgraded to 3.12.1",
        "Shipped in 2024.",
        "Worked on COVID-19 dashboards",
    ],
)
def test_names_versions_and_years_are_not_quantities(text: str) -> None:
    assert find_quantities(text) == []


def test_allowed_terms_are_skipped() -> None:
    assert find_quantities("Built on Java 17 and Python 3.12", ("Java 17", "python 3.12")) == []


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("minutes", "minute"),
        ("hrs", "hour"),
        ("hours per week", "hour"),
        ("percent", "%"),
        ("percentage points", "pp"),
        ("tickets", "ticket"),
        ("process", "process"),
        (None, None),
        ("  ", None),
    ],
)
def test_normalize_unit(unit: str | None, expected: str | None) -> None:
    assert normalize_unit(unit) == expected


def test_extract_metrics_from_an_existing_bullet() -> None:
    metrics = extract_metrics(
        "Raised throughput 5x (1,500 to 7,500 requests), saving 3--5 days per month."
    )
    assert [(type(m.value).__name__, m.unit, m.subject) for m in metrics] == [
        ("SingleValue", "x", "5x"),
        ("ChangeValue", "request", "1,500 to 7,500 requests"),
        ("RangeValue", "day", "3\u20135 days"),
    ]
