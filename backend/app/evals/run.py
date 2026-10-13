"""Run evaluation cases against the real models and score them (requirements §10).

Each case gets the numbers the evaluation bar is about:

- **Altered numbers:** numbers the source doesn't back. For extraction, each number the model
  wrote that the note doesn't give: code drops those facts, but the evaluation scores the prompt,
  so they count. For writing, each number error the verifier still finds in the final bullet.
  For adversarial cases, known-bad text with a wrong number that the verifier didn't catch.
- **Inflated claims:** claims that say more than the source. For extraction, each fact with a
  tool the note doesn't name, more ownership than its expected fact allows, or a match for a
  `never` pattern. For writing, each unsupported claim the verifier still finds in the final bullet,
  and each `never` match. For adversarial cases, other known-bad text the verifier didn't catch.
- **Fact recall:** for extraction, the expected facts that a kept fact matches, one to one.

A case's notes say what went wrong with case IDs, counts, pattern numbers and the verifier's
codes only: never text from a note, a fact or an output, which can be the owner's (NFR-PRIV-2).
The cost comes from the call log the client writes to.
"""

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import get_args

from app.evals.dataset import (
    AdversarialCase,
    Case,
    Dataset,
    ExpectedFact,
    ExtractionCase,
    Suite,
    WritingCase,
    writing_item,
)
from app.extraction import (
    ExtractedFact,
    ExtractionReply,
    RoleChoice,
    ask_for_facts,
    check_facts,
    names_tool,
)
from app.llm.client import LLMCallError, LLMClient
from app.llm.usage import CallRecord, MemoryCallLog
from app.schema.fact import ChangeValue, Fact, Ownership, RangeValue, SingleValue
from app.schema.jobs import JobPosting
from app.schema.profile import Profile
from app.schema.verification import VerifierReport
from app.validate.numbers import FindingCode
from app.validate.verifier import Verifier
from app.writing import write_bullet

BAR_RECALL = 0.9
"""The evaluation bar: 0 altered numbers, 0 inflated claims and at least 90% fact recall."""

NUMBER_CODES = frozenset(get_args(FindingCode))
"""The number check's codes. Known-bad text caught by one of these has a wrong number."""

_OWNERSHIP: dict[Ownership, int] = {"assisted": 0, "contributed": 1, "owned": 2, "led": 3}


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    passed: bool
    altered_numbers: int = 0
    inflated_claims: int = 0
    expected_facts: int = 0
    recovered_facts: int = 0
    notes: tuple[str, ...] = ()
    """What went wrong, or what's worth knowing, without any text from the case."""
    error: str | None = None
    """Why the case couldn't run, such as an unavailable API."""
    calls: tuple[CallRecord, ...] = ()


@dataclass(frozen=True)
class SuiteResult:
    suite: Suite
    cases: tuple[CaseResult, ...]

    @property
    def passed(self) -> int:
        return sum(case.passed for case in self.cases)

    @property
    def errors(self) -> int:
        return sum(case.error is not None for case in self.cases)

    @property
    def altered_numbers(self) -> int:
        return sum(case.altered_numbers for case in self.cases)

    @property
    def inflated_claims(self) -> int:
        return sum(case.inflated_claims for case in self.cases)

    @property
    def expected_facts(self) -> int:
        return sum(case.expected_facts for case in self.cases)

    @property
    def recovered_facts(self) -> int:
        return sum(case.recovered_facts for case in self.cases)

    @property
    def calls(self) -> tuple[CallRecord, ...]:
        return tuple(call for case in self.cases for call in case.calls)


@dataclass(frozen=True)
class Evaluation:
    suites: tuple[SuiteResult, ...]

    @property
    def cases(self) -> tuple[CaseResult, ...]:
        return tuple(case for suite in self.suites for case in suite.cases)

    @property
    def calls(self) -> tuple[CallRecord, ...]:
        return tuple(call for suite in self.suites for call in suite.calls)

    @property
    def recall(self) -> float | None:
        """The share of expected facts recovered, or None without any."""
        expected = sum(suite.expected_facts for suite in self.suites)
        recovered = sum(suite.recovered_facts for suite in self.suites)
        return recovered / expected if expected else None

    @property
    def meets_bar(self) -> bool:
        """Every case ran, with no altered number or inflated claim, and recall is at least 90%."""
        recall = self.recall
        return (
            not any(suite.errors for suite in self.suites)
            and not any(suite.altered_numbers for suite in self.suites)
            and not any(suite.inflated_claims for suite in self.suites)
            and (recall is None or recall >= BAR_RECALL)
        )


def cost_usd(calls: Sequence[CallRecord]) -> float | None:
    """The calls' estimated cost, or None when a tier has no price (`LLM_PRICE_*`)."""
    costs = [call.cost_usd for call in calls]
    if any(cost is None for cost in costs):
        return None
    return sum(cost for cost in costs if cost is not None)


def run_suite(
    dataset: Dataset,
    client: LLMClient,
    log: MemoryCallLog,
    progress: Callable[[CaseResult], None] | None = None,
) -> SuiteResult:
    """Run every case in `dataset` with `client`, which records its calls in `log`.

    A case whose call fails, such as one the API refuses, is reported with its error and the run
    goes on. A missing setting (`LLMConfigError`) stops the run.
    """
    results: list[CaseResult] = []
    for case in dataset.cases:
        start = len(log.calls)
        try:
            result = run_case(case, dataset, client)
        except LLMCallError as error:
            result = CaseResult(case_id=case.id, passed=False, error=str(error))
        result = replace(result, calls=tuple(log.calls[start:]))
        results.append(result)
        if progress is not None:
            progress(result)
    return SuiteResult(suite=dataset.suite, cases=tuple(results))


def run_case(case: Case, dataset: Dataset, client: LLMClient) -> CaseResult:
    match case:
        case ExtractionCase():
            return _extraction(case, dataset.profile, client)
        case WritingCase():
            return _writing(case, dataset, client)
        case AdversarialCase():
            return _adversarial(case, dataset, client)


# --- Extraction ------------------------------------------------------------------------------


@dataclass
class _ModelFact:
    """One fact the model wrote, and what's wrong with it."""

    reply: ExtractedFact
    kept: Fact | None
    unbacked: int
    problems: list[str] = field(default_factory=list[str])


def _extraction(case: ExtractionCase, profile: Profile, client: LLMClient) -> CaseResult:
    assert case.written is not None, "the dataset gives every extraction case its date"
    reply = ask_for_facts(case.note, profile, client, written=case.written)
    role = RoleChoice(role_id=case.role) if case.role else None
    facts: list[_ModelFact] = []
    for extracted in reply.facts:
        # Each fact is checked on its own, so what the checks keep is known fact by fact.
        checked = check_facts(ExtractionReply(facts=(extracted,)), case.note, profile, role=role)
        unbacked = sum(len(dropped.reasons) for dropped in checked.dropped)
        kept = checked.facts[0] if checked.facts else None
        facts.append(_ModelFact(reply=extracted, kept=kept, unbacked=unbacked))
    notes: list[str] = []
    for item in facts:
        if any(not names_tool(case.note, tool, profile.skills) for tool in item.reply.tools):
            item.problems.append("a tool the note doesn't name")
        for number, hit in _search(case.never, _fact_text(item.reply)):
            if hit:
                item.problems.append(f"never pattern {number}")
    unmatched = [item for item in facts if item.kept is not None]
    recovered = 0
    for number, expected in enumerate(case.expected, start=1):
        match = next(
            (item for item in unmatched if item.kept and _recovers(item.kept, expected)), None
        )
        if match is None:
            notes.append(f"missed expected fact {number}")
            continue
        recovered += 1
        unmatched.remove(match)
        if expected.ownership and _above(match.reply.ownership, expected.ownership):
            match.problems.append(f"ownership above {expected.ownership} (expected fact {number})")
    altered = sum(item.unbacked for item in facts)
    if altered:
        notes.append(_count(altered, "number") + " the note doesn't give")
    inflated = [item for item in facts if item.problems]
    for item in inflated:
        notes.append("a fact claims more than the note: " + ", ".join(item.problems))
    spurious = not case.expected and bool(reply.facts)
    if spurious:
        notes.append(_count(len(reply.facts), "fact") + " where none were expected")
    elif unmatched:
        notes.append(_count(len(unmatched), "extra fact"))
    return CaseResult(
        case_id=case.id,
        passed=recovered == len(case.expected) and not altered and not inflated and not spurious,
        altered_numbers=altered,
        inflated_claims=len(inflated),
        expected_facts=len(case.expected),
        recovered_facts=recovered,
        notes=tuple(notes),
    )


def _fact_text(fact: ExtractedFact) -> str:
    """What a fact claims, as one text for `never` patterns."""
    return "\n".join([fact.statement, fact.outcome or "", *fact.tools, fact.proposed_item or ""])


def _recovers(fact: Fact, expected: ExpectedFact) -> bool:
    text = "\n".join([fact.statement, fact.outcome or "", *fact.tools])
    if not all(re.search(pattern, text, re.IGNORECASE) for pattern in expected.terms):
        return False
    numbers = [number for metric in fact.metrics for number in _numbers(metric.value)]
    if not all(any(math.isclose(n, want) for n in numbers) for want in expected.numbers):
        return False
    if expected.role is not None and fact.role_id != expected.role:
        return False
    return expected.project is None or fact.project_id == expected.project


def _numbers(value: SingleValue | ChangeValue | RangeValue) -> tuple[float, ...]:
    match value:
        case SingleValue():
            return (value.value,)
        case ChangeValue():
            return (value.before, value.after)
        case RangeValue():
            return (value.low, value.high)


def _above(ownership: Ownership | None, ceiling: Ownership) -> bool:
    return ownership is not None and _OWNERSHIP[ownership] > _OWNERSHIP[ceiling]


# --- Writing ---------------------------------------------------------------------------------


def _writing(case: WritingCase, dataset: Dataset, client: LLMClient) -> CaseResult:
    item = writing_item(case, dataset)
    assert item is not None, "the dataset checks that every writing case has an item"
    report = write_bullet(
        [dataset.facts[fact_id] for fact_id in case.facts],
        dataset.profile,
        client,
        entry_id=item,
        edit=case.edit,
        avoid=case.avoid,
    )
    numbers = [finding for finding in report.errors if finding.check == "numbers"]
    claims = [
        finding
        for finding in report.errors
        if finding.check == "claims" and finding.code != "claims_unchecked"
    ]
    never = [number for number, hit in _search(case.never, report.text) if hit]
    lost = [number for number, hit in _search(case.keeps, report.text) if not hit]
    notes: list[str] = []
    if report.attempts == 2:
        notes.append("written twice")
    if not report.passed:
        notes.append("flagged: " + _codes(report))
    notes += [f"never pattern {number} matched" for number in never]
    notes += [f"keeps pattern {number} missing" for number in lost]
    return CaseResult(
        case_id=case.id,
        passed=report.passed and not never and not lost,
        altered_numbers=len(numbers),
        inflated_claims=len(claims) + len(never),
        notes=tuple(notes),
    )


# --- Adversarial -----------------------------------------------------------------------------


def _adversarial(case: AdversarialCase, dataset: Dataset, client: LLMClient) -> CaseResult:
    posting = None
    if case.posting_terms:
        posting = JobPosting(title="Job posting", key_terms=case.posting_terms)
    verifier = Verifier(
        client=client,
        profile=dataset.profile,
        facts=[dataset.facts[fact_id] for fact_id in case.facts],
        kind=case.kind,
        entry_id=writing_item(case, dataset),
        posting=posting,
    )
    report = verifier.check(case.text)
    if not case.catch:
        if report.passed:
            return CaseResult(case_id=case.id, passed=True)
        return CaseResult(case_id=case.id, passed=False, notes=("false alarm: " + _codes(report),))
    caught = sorted({finding.code for finding in report.errors} & set(case.catch))
    if caught:
        return CaseResult(case_id=case.id, passed=True, notes=("caught: " + ", ".join(caught),))
    number = bool(NUMBER_CODES & set(case.catch))
    return CaseResult(
        case_id=case.id,
        passed=False,
        altered_numbers=int(number),
        inflated_claims=int(not number),
        notes=("missed: " + (_codes(report) if report.errors else "it passed"),),
    )


def _search(patterns: Sequence[str], text: str) -> list[tuple[int, bool]]:
    """Each pattern's number, from 1, and whether it matches `text`, ignoring case."""
    return [
        (number, re.search(pattern, text, re.IGNORECASE) is not None)
        for number, pattern in enumerate(patterns, start=1)
    ]


def _codes(report: VerifierReport) -> str:
    """The codes of the report's errors, such as `added_tool, R3`."""
    return ", ".join(sorted({finding.code for finding in report.errors})) or "none"


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"
