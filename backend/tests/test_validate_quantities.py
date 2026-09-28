import pytest

from app.validate.quantities import Quantity, extract_metrics, find_quantities, normalize_unit


def only(text: str, allowed_terms: tuple[str, ...] = ()) -> Quantity:
    quantities = find_quantities(text, allowed_terms)
    assert len(quantities) == 1, quantities
    return quantities[0]


@pytest.mark.parametrize(
    ("text", "values", "unit", "qualifier", "kind"),
    [
        ("Reviewed 200+ papers", (200,), "paper", "at_least", "single"),
        ("Reviewed about 200 papers", (200,), "paper", "approximately", "single"),
        ("Reviewed ~200 papers", (200,), "paper", "approximately", "single"),
        ("served over 200 users", (200,), "user", "more_than", "single"),
        ("served more than 200 users", (200,), "user", "more_than", "single"),
        ("at least 10 teams", (10,), "team", "at_least", "single"),
        ("up to 3 hours", (3,), "hour", "at_most", "single"),
        ("in under 5 minutes", (5,), "minute", "less_than", "single"),
        ("200 or more tickets", (200,), "ticket", "at_least", "single"),
        ("improving F1 score by 33%", (33,), "%", "exact", "single"),
        ("improving F1 score by 33\\%", (33,), "%", "exact", "single"),
        ("accuracy of 93 percent", (93,), "%", "exact", "single"),
        ("raising the ceiling 6x", (6,), "x", "exact", "single"),
        ("4 times faster", (4,), "x", "exact", "single"),
        ("saving $1.2M a year", (1_200_000,), "$", "exact", "single"),
        ("a S$50k budget", (50_000,), "sgd", "exact", "single"),
        ("1,000+ training images", (1000,), "training", "at_least", "single"),
        ("across two departments", (2,), "department", "exact", "single"),
        ("a 12-person team", (12,), "person", "exact", "single"),
        ("1,000 to 6,000 items", (1000, 6000), "item", "exact", "pair"),
        ("from 8% to 2.1%", (8, 2.1), "%", "exact", "change"),
        ("from 50 to 12 minutes", (50, 12), "minute", "exact", "change"),
        ("2\u20133 hours per week", (2, 3), "hour", "exact", "range"),
        ("2--3 hours per week", (2, 3), "hour", "exact", "range"),
        ("2-3 hours per week", (2, 3), "hour", "exact", "range"),
        ("between 2 and 3 hours", (2, 3), "hour", "exact", "range"),
        ("hundreds of concurrent users", (200, 999), "concurrent", "exact", "range"),
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
    assert only("Reviewed about 200 papers").text == "about 200 papers"
    assert only("cut it from 50 to 12 minutes").text == "50 to 12 minutes"


def test_several_quantities_in_one_bullet() -> None:
    text = "raising the batch ceiling 6x (1,000 to 6,000 items) in 2 weeks"
    assert [(q.values, q.unit) for q in find_quantities(text)] == [
        ((6,), "x"),
        ((1000, 6000), "item"),
        ((2,), "week"),
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
        ("papers", "paper"),
        ("process", "process"),
        (None, None),
        ("  ", None),
    ],
)
def test_normalize_unit(unit: str | None, expected: str | None) -> None:
    assert normalize_unit(unit) == expected


def test_extract_metrics_from_an_existing_bullet() -> None:
    metrics = extract_metrics(
        "Raised the ceiling 6x (1,000 to 6,000 items), saving 2--3 hours per week."
    )
    assert [(type(m.value).__name__, m.unit, m.subject) for m in metrics] == [
        ("SingleValue", "x", "6x"),
        ("ChangeValue", "item", "1,000 to 6,000 items"),
        ("RangeValue", "hour", "2\u20133 hours"),
    ]
