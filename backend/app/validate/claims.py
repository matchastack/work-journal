"""Claim check: a standard-tier judge reads generated text against its facts (FR-FID-4..6).

The judge splits the text into claims and marks each one supported, unsupported or contradicted.
For a claim that isn't supported, it names the issue:

- inflated ownership, such as "led" for work the facts say the person contributed to;
- an added tool, team size, outcome or scope;
- a breach of an honesty boundary, or a skill on the gaps list;
- a term from a job posting that isn't a true synonym of what the facts say.

Numbers are left to the number check. Each claim that isn't supported is an error, and code
writes its message. What gets stored comes from the checked text, never from the facts: a claim
or term that isn't quoted from the text is replaced by the whole text, or dropped. The judge's
reasons can restate facts, so they serve only as feedback for a rewrite (NFR-SEC-2).

If the judge declines or its reply can't be used, the claims are unchecked, which is an error,
since text can't pass without the check. Errors that may clear up, such as an unavailable API,
are raised, so that a background job can try again.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.llm.client import LLMClient, LLMInvalidOutput, LLMRefusal
from app.llm.prompts import Prompt, load_prompt
from app.schema.common import Model, NonEmptyStr
from app.schema.fact import Fact
from app.schema.verification import ClaimIssue, ClaimJudgement, ClaimVerdict, VerifierFinding

PROMPT_NAME = "claim_verification"
PROMPT_VERSION = 1

_FACT_FIELDS = {"id", "statement", "kind", "metrics", "ownership", "tools", "outcome", "date"}
_ISSUES: dict[ClaimIssue, str] = {
    "inflated_ownership": "claims more ownership than the cited facts give",
    "added_tool": "names a tool the cited facts don't",
    "added_team_size": "adds a team size the cited facts don't give",
    "added_outcome": "adds an outcome the cited facts don't state",
    "added_scope": "makes the work bigger than the cited facts say",
    "honesty_boundary": "claims something the honesty boundaries rule out",
    "skill_gap": "claims a skill on the gaps list",
    "posting_term": "is a term from the posting that isn't a true synonym of what the facts say",
}


class JudgedClaim(Model):
    """One claim as the judge returns it. The reason comes before the verdict, so the judge writes
    it first."""

    claim: NonEmptyStr
    reason: NonEmptyStr
    verdict: ClaimVerdict
    issue: ClaimIssue | None = None
    term: str | None = None
    fact_ids: tuple[str, ...] = ()


class ClaimReply(Model):
    claims: tuple[JudgedClaim, ...]


@dataclass(frozen=True)
class ClaimCheck:
    judgements: tuple[ClaimJudgement, ...]
    findings: tuple[VerifierFinding, ...]
    """An error for each claim that isn't supported, or one saying the claims are unchecked."""
    feedback: tuple[str, ...]
    """For each claim that isn't supported, the problem and the judge's reason, as feedback for
    a rewrite. It can restate facts, so it must never be stored or logged."""
    prompt: str
    """The prompt's version, e.g. `claim_verification/v1`."""


def check_claims(
    text: str,
    facts: Sequence[Fact],
    client: LLMClient,
    *,
    boundaries: Sequence[str] = (),
    gaps: Sequence[str] = (),
    posting_terms: Sequence[str] = (),
    prompt: Prompt | None = None,
) -> ClaimCheck:
    """Check the claims in `text` against `facts`, the facts it was written from.

    `boundaries` are the honesty boundaries of the roles the text is about, `gaps` the skills on
    the gaps list, and `posting_terms` the terms of the job posting the text is tailored to.
    """
    prompt = prompt or load_prompt(PROMPT_NAME, PROMPT_VERSION)
    try:
        result = client.call(
            "claim_verification",
            prompt,
            ClaimReply,
            variables={
                "facts": _facts_json(facts),
                "boundaries": _listed(boundaries),
                "gaps": _listed(gaps),
                "posting_terms": _listed(posting_terms),
                "text": text,
            },
        )
    except (LLMRefusal, LLMInvalidOutput) as error:
        return _unchecked(f"the claims couldn't be checked: {error}", prompt)
    if not result.output.claims:
        return _unchecked("the claim check found no claims in the text", prompt)
    judgements: list[ClaimJudgement] = []
    findings: list[VerifierFinding] = []
    feedback: list[str] = []
    known = {fact.id for fact in facts}
    for judged in result.output.claims:
        judgement = _judgement(judged, text, known)
        judgements.append(judgement)
        if judgement.verdict == "supported":
            continue
        finding = _finding(judgement, facts)
        findings.append(finding)
        feedback.append(f'{finding.message}. On "{judged.claim}": {judged.reason}')
    return ClaimCheck(
        judgements=tuple(judgements),
        findings=tuple(findings),
        feedback=tuple(feedback),
        prompt=prompt.id,
    )


def _facts_json(facts: Sequence[Fact]) -> str:
    data = [fact.model_dump(mode="json", include=_FACT_FIELDS, exclude_none=True) for fact in facts]
    return json.dumps(data, indent=2, ensure_ascii=False)


def _listed(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "none"


def _judgement(judged: JudgedClaim, text: str, known: set[str]) -> ClaimJudgement:
    supported = judged.verdict == "supported"
    term = judged.term if judged.term and _quotes(judged.term, text) else None
    return ClaimJudgement(
        claim=judged.claim if _quotes(judged.claim, text) else text,
        verdict=judged.verdict,
        issue=None if supported else judged.issue or "other",
        term=None if supported else term,
        fact_ids=tuple(dict.fromkeys(fact_id for fact_id in judged.fact_ids if fact_id in known)),
    )


def _finding(judgement: ClaimJudgement, facts: Sequence[Fact]) -> VerifierFinding:
    issue = judgement.issue or "other"
    if issue == "other":
        code = f"{judgement.verdict}_claim"
        what = (
            "contradicts the cited facts"
            if judgement.verdict == "contradicted"
            else "isn't supported by the cited facts"
        )
        message = f'"{judgement.claim}" {what}'
    else:
        code = issue
        message = f'"{judgement.term or judgement.claim}" {_ISSUES[issue]}'
        if issue == "inflated_ownership" and (levels := _ownership(judgement, facts)):
            message += f" ({' or '.join(levels)})"
    return VerifierFinding(check="claims", severity="error", code=code, message=message)


def _ownership(judgement: ClaimJudgement, facts: Sequence[Fact]) -> list[str]:
    """The ownership levels of the facts the claim rests on, or else of all the cited facts."""
    cited = [fact for fact in facts if fact.id in judgement.fact_ids] or list(facts)
    return list(dict.fromkeys(fact.ownership for fact in cited if fact.ownership))


def _quotes(part: str, text: str) -> bool:
    """Whether `part` is quoted from `text`, ignoring case, spacing and surrounding punctuation."""
    quoted = _normalized(part).strip(" .,;:!?\"'")
    return bool(quoted) and quoted in _normalized(text)


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _unchecked(message: str, prompt: Prompt) -> ClaimCheck:
    finding = VerifierFinding(
        check="claims", severity="error", code="claims_unchecked", message=message
    )
    return ClaimCheck(judgements=(), findings=(finding,), feedback=(), prompt=prompt.id)
