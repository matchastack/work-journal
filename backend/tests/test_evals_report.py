from datetime import UTC, date, datetime

from app.evals.report import evaluation_report
from app.evals.run import CaseResult, Evaluation, SuiteResult
from app.llm.usage import CallRecord

ON = date(2026, 10, 13)


def call(prompt: str, *, cost: float | None = 0.0006, **fields: object) -> CallRecord:
    values: dict[str, object] = {
        "at": datetime(2026, 10, 13, tzinfo=UTC),
        "task": prompt.split("/")[0],
        "tier": "standard",
        "model": "standard-model",
        "prompt": prompt,
        "outcome": "ok",
        "attempts": 1,
        "input_tokens": 100,
        "output_tokens": 20,
        "cost_usd": cost,
        "latency_ms": 900,
    }
    return CallRecord.model_validate(values | fields)


EXTRACTED = call("fact_extraction/v1")
WRITTEN = call("bullet_writing/v1")
JUDGED = call("claim_verification/v1")


def report(*suites: SuiteResult) -> str:
    return evaluation_report(Evaluation(suites=suites), dataset="The fictional cases", on=ON)


def test_a_report_that_meets_the_bar() -> None:
    extraction = SuiteResult(
        suite="extraction",
        cases=(
            CaseResult(
                "a work note", True, expected_facts=1, recovered_facts=1, calls=(EXTRACTED,)
            ),
            CaseResult("chit chat", True, calls=(EXTRACTED,)),
        ),
    )
    writing = SuiteResult(
        suite="writing",
        cases=(
            CaseResult("numbers kept", True, notes=("written twice",), calls=(WRITTEN, JUDGED)),
        ),
    )
    assert report(extraction, writing) == (
        "# Evaluation report\n"
        "\n"
        "The fictional cases, run on 2026-10-13.\n"
        "\n"
        "- Prompts: bullet_writing/v1, claim_verification/v1, fact_extraction/v1\n"
        "- Models: standard `standard-model`\n"
        "\n"
        "**Meets the bar.**\n"
        "\n"
        "The bar for changing a prompt or the model routing (requirements section 10): 0 altered "
        "numbers, 0 inflated claims and at least 90% of facts recovered, with every case run.\n"
        "\n"
        "| Suite | Cases passed | Altered numbers | Inflated claims | Fact recall | Calls | "
        "Cost (USD) |\n"
        "|---|---|---|---|---|---|---|\n"
        "| extraction | 2/2 | 0 | 0 | 1/1 (100%) | 2 | 0.0012 |\n"
        "| writing | 1/1 | 0 | 0 | - | 2 | 0.0012 |\n"
        "| **Total** | 3/3 | 0 | 0 | 1/1 (100%) | 4 | 0.0024 |\n"
        "\n"
        "Cost is estimated at the tier prices in `LLM_PRICE_*`.\n"
        "\n"
        "## extraction\n"
        "\n"
        "| Case | Result | Notes |\n"
        "|---|---|---|\n"
        "| a work note | pass |  |\n"
        "| chit chat | pass |  |\n"
        "\n"
        "## writing\n"
        "\n"
        "| Case | Result | Notes |\n"
        "|---|---|---|\n"
        "| numbers kept | pass | written twice |\n"
    )


def test_a_report_that_misses_the_bar_says_by_what() -> None:
    suite = SuiteResult(
        suite="extraction",
        cases=(
            CaseResult(
                "invented",
                False,
                altered_numbers=2,
                expected_facts=9,
                recovered_facts=8,
                notes=("missed expected fact 1", "2 numbers the note doesn't give"),
            ),
            CaseResult("instructions", False, inflated_claims=1, notes=("a | b",)),
            CaseResult("unavailable", False, error="the Claude API is unavailable"),
        ),
    )
    text = report(suite)
    assert (
        "**Doesn't meet the bar:** 2 altered numbers; 1 inflated claim; fact recall 88.8%; "
        "1 case couldn't run.\n"
    ) in text
    assert "| extraction | 0/3 | 2 | 1 | 8/9 (88.8%) | 0 | 0.0000 |\n" in text
    assert "**Total**" not in text, "one suite needs no total"
    assert (
        "| invented | **fail** | missed expected fact 1; 2 numbers the note doesn't give |\n"
    ) in text
    assert "| instructions | **fail** | a \\| b |\n" in text
    assert "| unavailable | error | the Claude API is unavailable |\n" in text


def test_recall_just_under_the_bar_never_shows_as_on_it() -> None:
    cases = (CaseResult("a", False, expected_facts=1000, recovered_facts=899),)
    assert "899/1000 (89.9%)" in report(SuiteResult(suite="extraction", cases=cases))


def test_without_prices_the_cost_is_left_out() -> None:
    cases = (CaseResult("a", True, calls=(EXTRACTED, call("fact_extraction/v1", cost=None))),)
    text = report(SuiteResult(suite="extraction", cases=cases))
    assert "| extraction | 1/1 | 0 | 0 | - | 2 | - |\n" in text
    assert "Set `LLM_PRICE_*` for each tier to estimate the cost.\n" in text


def test_calls_answered_by_the_fallback_model_are_counted() -> None:
    fallback = call("fact_extraction/v1", fell_back=True, served_by="fallback-model")
    text = report(
        SuiteResult(suite="extraction", cases=(CaseResult("a", True, calls=(fallback,)),))
    )
    assert "- 1 call answered by the API's fallback model\n" in text


def test_the_report_is_ascii() -> None:
    cases = (CaseResult("a", False, inflated_claims=1, calls=(EXTRACTED,)),)
    assert report(SuiteResult(suite="extraction", cases=cases)).isascii()
