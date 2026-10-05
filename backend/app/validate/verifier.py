"""The fidelity verifier: every generated sentence passes it before anyone sees it (FR-FID, §10).

`Verifier.check(text)` runs four checks on one text and returns a `VerifierReport`:

- numbers (FR-FID-1..3): every number is backed by a metric of the facts the text was written
  from, and a figure code derives shows its formula (T-005);
- claims (FR-FID-4..6): the standard-tier judge finds claims the facts don't support
  (`app/validate/claims.py`);
- style (FR-WRT-1, 2 and 4): the wording rules for a resume bullet or LinkedIn text (T-013);
- rules (§8): the resume rules a single text can break, such as the gaps list (T-009).

`Verifier.write(writer)` gets text from a writer and checks it. Text that fails is written once
more, with the errors as feedback; when no error is one a rewrite can fix, such as claims the
judge couldn't check, there's no rewrite. Text that still fails is flagged (`passed` is false),
and the owner must accept it by hand (FR-FID-7).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.llm.client import LLMClient
from app.schema.fact import Fact
from app.schema.jobs import JobPosting
from app.schema.profile import Profile
from app.schema.verification import VerifierFinding, VerifierReport
from app.text import plain
from app.validate.claims import check_claims
from app.validate.lint import allowed_terms, check_text
from app.validate.numbers import check_numbers
from app.validate.style import LinkedInSection, StyleCheck, check_linkedin, check_resume_bullet

TextKind = Literal["resume_bullet", "linkedin_headline", "linkedin_about", "linkedin_position"]

Writer = Callable[[str | None], str]
"""Writes the text: first with no feedback, then, for the one rewrite, with the verifier's."""

_LINKEDIN: dict[TextKind, LinkedInSection] = {
    "linkedin_headline": "headline",
    "linkedin_about": "about",
    "linkedin_position": "position",
}


@dataclass(frozen=True)
class Verifier:
    """Checks text written from `facts` for `profile`, as one kind of text."""

    client: LLMClient
    profile: Profile
    facts: Sequence[Fact]
    kind: TextKind = "resume_bullet"
    entry_id: str | None = None
    """The role, education entry or project the text is about. A resume bullet goes under it."""
    replaces: str | None = None
    """The ID of the bullet the text replaces, if it's an edit."""
    posting: JobPosting | None = None
    """The job posting the text is tailored to, if any (FR-FID-6)."""
    avoid: Sequence[str] = ()
    """The owner's words to avoid (FR-WRT-4)."""

    def __post_init__(self) -> None:
        entries = {
            *(role.id for role in self.profile.work),
            *(entry.id for entry in self.profile.education),
            *(project.id for project in self.profile.projects),
        }
        if self.entry_id is not None and self.entry_id not in entries:
            raise ValueError(
                f"the profile has no role, education entry or project {self.entry_id!r}"
            )

    def check(self, text: str) -> VerifierReport:
        """Run every check on `text`."""
        return self._check(text, attempts=1)[0]

    def write(self, writer: Writer) -> VerifierReport:
        """Get text from `writer` and check it, writing it once more if it fails (FR-FID-7)."""
        report, feedback = self._check(writer(None), attempts=1)
        if report.passed or not feedback:
            return report
        return self._check(writer(feedback), attempts=2)[0]

    def _check(self, text: str, *, attempts: int) -> tuple[VerifierReport, str]:
        """The report on `text`, and the feedback a rewrite would get: one line per error."""
        text = plain(text).strip()
        if not text:
            raise ValueError("there's no text to check")
        metrics = [metric for fact in self.facts for metric in fact.metrics]
        numbers = check_numbers(text, metrics, allowed_terms(self.profile))
        claims = check_claims(
            text,
            self.facts,
            self.client,
            boundaries=self._boundaries(),
            gaps=self.profile.skill_gaps,
            posting_terms=self._posting_terms(),
        )
        style = self._style(text)
        bullet = self.kind == "resume_bullet"
        rules = check_text(
            text,
            self.profile,
            entry_id=self.entry_id if bullet else None,
            replaces=self.replaces if bullet else None,
        )
        number_findings = [
            VerifierFinding(check="numbers", severity=f.severity, code=f.code, message=f.message)
            for f in numbers.findings
        ]
        style_findings = [
            VerifierFinding(check="style", severity=f.severity, code=f.code, message=f.message)
            for f in style.findings
        ]
        rule_findings = [
            VerifierFinding(check="rules", severity=f.severity, code=f.rule, message=f.message)
            for f in rules.findings
        ]
        findings = [*number_findings, *claims.findings, *style_findings, *rule_findings]
        report = VerifierReport(
            text=text,
            passed=not any(finding.severity == "error" for finding in findings),
            attempts=attempts,
            fact_ids=tuple(fact.id for fact in self.facts),
            findings=tuple(sorted(findings, key=lambda finding: finding.severity != "error")),
            claims=claims.judgements,
            derivations=numbers.derivations,
            claim_prompt=claims.prompt,
        )
        problems = [
            *_errors(number_findings),
            *claims.feedback,
            *_errors(style_findings),
            *_errors(rule_findings),
        ]
        return report, "\n".join(f"- {problem}" for problem in problems)

    def _boundaries(self) -> list[str]:
        """The honesty boundaries of the role the text is about, and of the facts' roles."""
        roles = {self.entry_id, *(fact.role_id for fact in self.facts)}
        return [
            boundary
            for role in self.profile.work
            if role.id in roles
            for boundary in role.honesty_boundaries
        ]

    def _posting_terms(self) -> list[str]:
        if self.posting is None:
            return []
        posting = self.posting
        return list(dict.fromkeys([*posting.key_terms, *posting.must_have, *posting.nice_to_have]))

    def _style(self, text: str) -> StyleCheck:
        if self.kind == "resume_bullet":
            return check_resume_bullet(text, avoid=self.avoid)
        return check_linkedin(text, _LINKEDIN[self.kind], avoid=self.avoid)


def _errors(findings: Sequence[VerifierFinding]) -> list[str]:
    return [finding.message for finding in findings if finding.severity == "error"]
