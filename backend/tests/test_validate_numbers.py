import pytest

from app.schema.fact import ChangeValue, Metric, RangeValue, SingleValue
from app.validate.numbers import check_numbers
from app.validate.quantities import extract_metrics


def metric(value: SingleValue | ChangeValue | RangeValue, unit: str | None = None, **kw) -> Metric:
    return Metric(subject=kw.pop("subject", "m"), value=value, unit=unit, **kw)


EXPORT = metric(ChangeValue(before=50, after=12), "minutes", subject="export duration")
PAPERS = metric(SingleValue(value=200), "papers", qualifier="at_least", subject="papers")


def codes(text: str, metrics: list[Metric], allowed: tuple[str, ...] = ()) -> list[str]:
    return [finding.code for finding in check_numbers(text, metrics, allowed).findings]


def test_supported_numbers_pass_cleanly() -> None:
    result = check_numbers("Cut the export from 50 to 12 minutes.", [EXPORT])
    assert result.ok
    assert result.findings == ()


def test_latex_escaped_percent_is_supported() -> None:
    f1 = metric(SingleValue(value=33), "%", subject="F1 improvement")
    assert check_numbers("improving F1 score by 33\\%", [f1]).findings == ()


def test_invented_number_is_an_error() -> None:
    result = check_numbers("Cut the export from 50 to 10 minutes.", [EXPORT])
    assert not result.ok
    assert "unsupported_number" in [finding.code for finding in result.findings]


def test_number_with_no_cited_facts_is_an_error() -> None:
    assert codes("Served 5,000 users.", []) == ["unsupported_number"]


def test_changed_unit_is_an_error() -> None:
    assert codes("Cut the export from 50 to 12 hours.", [EXPORT]) == ["unit_changed"]


def test_stronger_qualifier_is_an_error() -> None:
    assert codes("Reviewed over 200 papers", [PAPERS]) == ["qualifier_stronger"]
    approx = metric(SingleValue(value=200), "papers", qualifier="approximately")
    assert codes("Reviewed 200 papers", [approx]) == ["qualifier_stronger"]


def test_weaker_qualifier_is_a_warning() -> None:
    exact = metric(SingleValue(value=200), "papers")
    result = check_numbers("Reviewed about 200 papers", [exact])
    assert result.ok
    assert [finding.code for finding in result.findings] == ["qualifier_weakened"]


def test_same_qualifier_passes() -> None:
    assert codes("Reviewed 200+ papers", [PAPERS]) == []
    assert codes("Reviewed at least 200 papers", [PAPERS]) == []


@pytest.mark.parametrize(
    ("text", "formula_start"),
    [
        ("Made the export 76% faster.", "|12 - 50| / 50"),
        ("Made the export 4x faster.", "50 / 12"),
        ("Made the export 38 minutes faster.", "|12 - 50|"),
    ],
)
def test_figures_derived_by_code_are_accepted_with_a_formula(text: str, formula_start: str) -> None:
    result = check_numbers(text, [EXPORT])
    assert result.ok
    assert result.findings == ()
    assert result.derivations[0].formula.startswith(formula_start)


def test_wrong_derived_figure_is_an_error() -> None:
    assert codes("Made the export 80% faster.", [EXPORT]) == [
        "unsupported_number",
        "metric_dropped",
    ]


def test_one_end_of_a_change_can_be_cited_alone() -> None:
    assert codes("The export now takes 12 minutes.", [EXPORT]) == []


def test_pair_matches_a_change_or_a_range() -> None:
    ceiling = metric(ChangeValue(before=1000, after=6000), "items")
    assert codes("from a ceiling of 1,000 to 6,000 items", [ceiling]) == []
    assert codes("1,000 to 6,000 items", [ceiling]) == []
    weekly = metric(RangeValue(low=2, high=3), "hours")
    assert codes("saving 2 to 3 hours a week", [weekly]) == []
    assert codes("saving 2\u20133 hours a week", [weekly]) == []


def test_dropped_metric_is_a_warning() -> None:
    result = check_numbers("Reviewed the literature.", [PAPERS])
    assert result.ok
    assert [finding.code for finding in result.findings] == ["metric_dropped"]


def test_vague_magnitudes_cannot_be_inflated() -> None:
    users = metric(RangeValue(low=200, high=999), "users", subject="concurrent users")
    assert codes("serving hundreds of users", [users]) == []
    assert codes("serving thousands of users", [users]) == ["unsupported_number", "metric_dropped"]


def test_allowed_terms_are_not_checked() -> None:
    assert codes("Built services on Java 17.", [], allowed=("Java 17",)) == []


def test_word_numbers_are_checked() -> None:
    departments = metric(SingleValue(value=2), "departments")
    assert codes("across two departments", [departments]) == []
    assert codes("across three departments", [departments]) == [
        "unsupported_number",
        "metric_dropped",
    ]


def test_a_bullet_passes_against_its_own_extracted_metrics() -> None:
    text = "Raised the ceiling 6x (1,000 to 6,000 items), saving 2--3 hours per week."
    assert check_numbers(text, extract_metrics(text)).findings == ()
