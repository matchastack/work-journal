"""Verifier reports: what the fidelity verifier found in one generated text (FR-FID).

A report is stored with each proposed change and each application (FR-FID-8). It holds
structured results only. The claim check's verdicts quote the claim from the checked text and
name facts by ID, and code writes every message, so a report never holds fact text, which is
stored encrypted (NFR-SEC-2).
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from app.schema.common import Id, Model, NonEmptyStr

VerifierCheck = Literal["numbers", "claims", "style", "rules"]
"""The number check (FR-FID-1..3), the claim check (FR-FID-4..6), the style check (FR-WRT) and
the resume rules (requirements §8)."""

ClaimVerdict = Literal["supported", "unsupported", "contradicted"]

ClaimIssue = Literal[
    "inflated_ownership",
    "added_tool",
    "added_team_size",
    "added_outcome",
    "added_scope",
    "honesty_boundary",
    "skill_gap",
    "posting_term",
    "other",
]
"""Why a claim isn't supported (FR-FID-4..6)."""


class VerifierFinding(Model):
    check: VerifierCheck
    severity: Literal["error", "warning"]
    code: NonEmptyStr
    """The rule that fired, e.g. `unsupported_number`, `inflated_ownership`, `first_person` or
    `R3`."""
    message: NonEmptyStr


class ClaimJudgement(Model):
    """The claim check's verdict on one claim in the text."""

    claim: NonEmptyStr
    """The claim, in the checked text's own words."""
    verdict: ClaimVerdict
    issue: ClaimIssue | None = None
    """Why the claim isn't supported. None for a supported claim."""
    term: str | None = None
    """The word or phrase that causes the issue, e.g. the added tool."""
    fact_ids: tuple[Id, ...] = ()
    """The facts the claim rests on, or that contradict it."""


class Derivation(Model):
    """A figure in the text that code computed from a fact metric, and how (FR-FID-2)."""

    quantity: str
    formula: str


class VerifierReport(Model):
    """What the verifier found in one text. Text that doesn't pass is flagged: it's shown to the
    owner, who must accept it by hand (FR-FID-7)."""

    text: NonEmptyStr
    """The text checked. After a rewrite, this is the rewritten text."""
    passed: bool
    """True when no check found an error. Warnings are allowed."""
    attempts: int = Field(default=1, ge=1, le=2)
    """2 when the first text failed and was written again with the findings as feedback."""
    fact_ids: tuple[Id, ...] = ()
    """The facts the text was written from."""
    findings: tuple[VerifierFinding, ...] = ()
    """Errors first, then warnings."""
    claims: tuple[ClaimJudgement, ...] = ()
    derivations: tuple[Derivation, ...] = ()
    claim_prompt: str | None = None
    """The claim check's prompt version, e.g. `claim_verification/v1`."""

    @model_validator(mode="after")
    def passed_means_no_errors(self) -> Self:
        if self.passed == any(finding.severity == "error" for finding in self.findings):
            raise ValueError("a report passes exactly when it has no errors")
        return self

    @property
    def errors(self) -> tuple[VerifierFinding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == "error")
