"""Opt-in evaluation of the verifier on known-bad and faithful text about the fictional person.

Each known-bad text breaks one rule, such as inflating ownership or inventing a number, and must
fail with that rule's code. Each faithful text must pass. The claim check calls the
standard-tier model, so these run only with `pytest -m llm` and need ANTHROPIC_API_KEY and
LLM_MODEL_STANDARD.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from app.config import Settings
from app.llm.client import LLMClient
from app.schema.fact import Fact
from app.schema.jobs import JobPosting
from app.schema.profile import Profile
from app.schema.verification import VerifierReport
from app.validate.verifier import TextKind, Verifier

pytestmark = pytest.mark.llm

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
FACTS = {
    fact.id: fact
    for fact in TypeAdapter(tuple[Fact, ...]).validate_json(
        (FIXTURES / "facts.json").read_text(encoding="utf-8")
    )
}
EXPORT, QUEUE = "fact_nw_export", "fact_nw_queue"


@dataclass(frozen=True)
class Case:
    text: str
    facts: tuple[str, ...]
    codes: frozenset[str] = frozenset()
    """For known-bad text, the codes that catch it, any one of which will do."""
    check: str = "claims"
    posting_terms: tuple[str, ...] = ()
    kind: TextKind = "resume_bullet"


KNOWN_BAD = {
    "inflated ownership": Case(
        "Led the move of order processing from a cron job to a message queue.",
        (QUEUE,),
        frozenset({"inflated_ownership"}),
    ),
    "added tool": Case(
        "Cut the nightly export job from 50 to 12 minutes by batching writes with Apache Spark.",
        (EXPORT,),
        frozenset({"added_tool"}),
    ),
    "added team size": Case(
        "Led a team of engineers that cut the nightly export job from 50 to 12 minutes.",
        (EXPORT,),
        frozenset({"added_team_size", "inflated_ownership"}),
    ),
    "added outcome": Case(
        "Cut the nightly export job from 50 to 12 minutes by batching database writes, "
        "raising customer satisfaction.",
        (EXPORT,),
        frozenset({"added_outcome"}),
    ),
    "added scope": Case(
        "Cut every export job across the company from 50 to 12 minutes by batching database "
        "writes.",
        (EXPORT,),
        frozenset({"added_scope"}),
    ),
    "honesty boundary": Case(
        "Owned the deployment pipeline for the nightly export job, cutting it from 50 to 12 "
        "minutes.",
        (EXPORT,),
        frozenset({"honesty_boundary", "contradicted_claim"}),
    ),
    "skill on the gaps list": Case(
        "Rewrote the nightly export job in Rust, cutting it from 50 to 12 minutes.",
        (EXPORT,),
        frozenset({"skill_gap", "added_tool"}),
    ),
    "posting term that isn't a synonym": Case(
        "Scaled a distributed system by cutting its nightly export job from 50 to 12 minutes.",
        (EXPORT,),
        frozenset({"posting_term", "added_scope"}),
        posting_terms=("distributed systems",),
    ),
    "invented number": Case(
        "Cut the nightly export job from 50 to 8 minutes by batching database writes.",
        (EXPORT,),
        frozenset({"unsupported_number"}),
        check="numbers",
    ),
}

FAITHFUL = {
    "close rewording": Case(
        "Reduced the nightly export job from 50 to 12 minutes by batching PostgreSQL writes.",
        (EXPORT,),
    ),
    "derived figure and stated outcome": Case(
        "Cut the nightly export job's runtime by 76% by batching database writes, so it "
        "finished before the morning reports.",
        (EXPORT,),
    ),
    "modest ownership": Case(
        "Helped move order processing from a cron job to a RabbitMQ message queue.", (QUEUE,)
    ),
    "posting term that is a true synonym": Case(
        "Cut the nightly export job from 50 to 12 minutes by batching writes to a relational "
        "database.",
        (EXPORT,),
        posting_terms=("relational databases",),
    ),
    "LinkedIn in the first person": Case(
        "I helped move our order processing from a cron job to a message queue on RabbitMQ.",
        (QUEUE,),
        kind="linkedin_position",
    ),
}


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> LLMClient:
    settings = Settings()
    if settings.anthropic_api_key is None or settings.llm_model_standard is None:
        pytest.skip("set ANTHROPIC_API_KEY and LLM_MODEL_STANDARD to call the API")
    log = tmp_path_factory.mktemp("llm") / "calls.jsonl"
    return LLMClient.from_settings(settings.model_copy(update={"llm_call_log": log}))


def verify(case: Case, client: LLMClient) -> VerifierReport:
    posting = None
    if case.posting_terms:
        posting = JobPosting(title="Backend Engineer", key_terms=case.posting_terms)
    verifier = Verifier(
        client=client,
        profile=PROFILE,
        facts=[FACTS[fact_id] for fact_id in case.facts],
        kind=case.kind,
        entry_id="northwind",
        posting=posting,
    )
    return verifier.check(case.text)


@pytest.mark.parametrize("case", KNOWN_BAD.values(), ids=KNOWN_BAD.keys())
def test_known_bad_text_fails_with_the_rule_it_breaks(case: Case, client: LLMClient) -> None:
    report = verify(case, client)
    assert not report.passed
    codes = {finding.code for finding in report.errors if finding.check == case.check}
    assert codes & case.codes, [finding.message for finding in report.findings]


@pytest.mark.parametrize("case", FAITHFUL.values(), ids=FAITHFUL.keys())
def test_faithful_text_passes(case: Case, client: LLMClient) -> None:
    report = verify(case, client)
    assert report.passed, [finding.message for finding in report.errors]
