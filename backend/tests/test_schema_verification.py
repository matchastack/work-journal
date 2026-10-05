from typing import Any

import pytest
from pydantic import ValidationError

from app.schema.verification import VerifierReport

ERROR = {
    "check": "claims",
    "severity": "error",
    "code": "inflated_ownership",
    "message": '"Led" claims more ownership than the cited facts give (contributed)',
}
WARNING = {
    "check": "numbers",
    "severity": "warning",
    "code": "metric_dropped",
    "message": "the fact's metric 'nightly export duration: 50 -> 12 minutes' isn't mentioned",
}


def report(**fields: Any) -> VerifierReport:
    return VerifierReport.model_validate({"text": "Led the move to a message queue.", **fields})


def test_a_report_round_trips_as_camel_case_json() -> None:
    flagged = report(
        passed=False,
        attempts=2,
        factIds=["fact_nw_queue"],
        findings=[ERROR, WARNING],
        claims=[
            {
                "claim": "Led the move to a message queue",
                "verdict": "unsupported",
                "issue": "inflated_ownership",
                "term": "Led",
                "factIds": ["fact_nw_queue"],
            }
        ],
        derivations=[{"quantity": "76%", "formula": "|12 - 50| / 50 = 0.76"}],
        claimPrompt="claim_verification/v1",
    )
    data = flagged.model_dump(mode="json")
    assert set(data) == {
        "text", "passed", "attempts", "factIds", "findings", "claims", "derivations",
        "claimPrompt",
    }  # fmt: skip
    assert VerifierReport.model_validate(data) == flagged
    assert flagged.errors == (flagged.findings[0],)


@pytest.mark.parametrize(
    ("passed", "findings"),
    [(True, [ERROR]), (False, []), (False, [WARNING])],
)
def test_a_report_passes_exactly_when_it_has_no_errors(
    passed: bool, findings: list[dict[str, str]]
) -> None:
    with pytest.raises(ValidationError, match="passes exactly when it has no errors"):
        report(passed=passed, findings=findings)
    assert report(passed=not passed, findings=findings).passed is not passed


def test_a_text_is_written_at_most_twice() -> None:
    with pytest.raises(ValidationError):
        report(passed=True, attempts=3)
