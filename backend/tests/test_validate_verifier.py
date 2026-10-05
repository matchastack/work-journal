from pathlib import Path
from typing import Any

import pytest
from anthropic.types.beta import BetaMessage
from pydantic import TypeAdapter

from app.config import Settings
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, refusal, reply
from app.llm.usage import MemoryCallLog
from app.schema.fact import Fact
from app.schema.jobs import JobPosting
from app.schema.profile import Profile
from app.schema.verification import VerifierReport
from app.validate.claims import ClaimReply, JudgedClaim
from app.validate.verifier import Verifier

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
FACTS = TypeAdapter(tuple[Fact, ...]).validate_json(
    (FIXTURES / "facts.json").read_text(encoding="utf-8")
)
EXPORT, QUEUE, RECIPES = FACTS
GOOD = "Reduced the nightly export job from 50 to 12 minutes by batching PostgreSQL writes."
SUPPORTED = ClaimReply(
    claims=(
        JudgedClaim(
            claim="Reduced the nightly export job",
            reason="The fact says the export went from 50 to 12 minutes.",
            verdict="supported",
            fact_ids=("fact_nw_export",),
        ),
    )
)


def added_tool(tool: str) -> ClaimReply:
    claim = JudgedClaim(
        claim=f"using {tool}",
        reason=f"No fact names {tool}.",
        verdict="unsupported",
        issue="added_tool",
        term=tool,
    )
    return ClaimReply(claims=(claim,))


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def make(*replies: ClaimReply | BetaMessage, **options: Any) -> tuple[Verifier, FakeMessages]:
    fake = FakeMessages(*(r if isinstance(r, BetaMessage) else reply(r) for r in replies))
    client = LLMClient(Settings(llm_model_standard="standard-model"), fake, MemoryCallLog())
    options = {"facts": (EXPORT,), "entry_id": "northwind", **options}
    return Verifier(client=client, profile=PROFILE, **options), fake


def found(report: VerifierReport) -> list[tuple[str, str, str]]:
    return [(f.check, f.severity, f.code) for f in report.findings]


def test_text_that_passes_every_check() -> None:
    verifier, _ = make(SUPPORTED)
    report = verifier.check(GOOD)
    assert report.passed
    assert report.findings == ()
    assert report.attempts == 1
    assert report.fact_ids == ("fact_nw_export",)
    assert [claim.claim for claim in report.claims] == ["Reduced the nightly export job"]
    assert report.claim_prompt == "claim_verification/v1"
    assert VerifierReport.model_validate(report.model_dump(mode="json")) == report


def test_a_figure_code_derives_is_shown_with_its_formula() -> None:
    verifier, _ = make(SUPPORTED)
    report = verifier.check("Cut the nightly export job's runtime by 76% by batching writes.")
    assert report.passed
    assert [d.formula for d in report.derivations] == ["|12 - 50| / 50 = 76"]


def test_each_check_reports_its_errors_and_warnings_come_last() -> None:
    verifier, _ = make(added_tool("Kafka"), facts=(EXPORT, QUEUE))
    text = "Led the move to Kafka for 500 users, which I wrote in Rust."
    report = verifier.check(text)
    assert not report.passed
    assert found(report) == [
        ("numbers", "error", "unsupported_number"),
        ("claims", "error", "added_tool"),
        ("style", "error", "first_person"),
        ("rules", "error", "R3"),
        ("numbers", "warning", "metric_dropped"),
    ]
    assert [f.message for f in report.errors] == [
        "'500 users' isn't in the cited facts",
        '"Kafka" names a tool the cited facts don\'t',
        'Uses first-person pronouns ("I").',
        "Rust is on the gaps list, so it must never be claimed",
    ]


def test_a_bullet_is_checked_against_the_rest_of_the_profile() -> None:
    copy = "Cut the nightly export job from 50 to 12 minutes by batching database writes."
    verifier, _ = make(SUPPORTED, entry_id="contoso")
    assert found(verifier.check(copy)) == [
        ("rules", "warning", "R4"),
        ("rules", "warning", "duplicates"),
    ]
    edit, _ = make(SUPPORTED, replaces="nw_export")
    assert edit.check(copy).findings == (), "an edit replaces the bullet it repeats"


def test_linkedin_text_is_checked_as_linkedin_text() -> None:
    verifier, _ = make(SUPPORTED, SUPPORTED, kind="linkedin_position", facts=(QUEUE,))
    text = "I helped move our order processing from a cron job to a message queue."
    assert verifier.check(text).findings == (), "the first person is fine on LinkedIn"
    headline, _ = make(SUPPORTED, kind="linkedin_headline", facts=(QUEUE,))
    report = headline.check("Backend engineer " * 20)
    assert found(report) == [("style", "error", "too_long")]


def test_the_judge_gets_the_boundaries_gaps_and_posting_terms() -> None:
    posting = JobPosting(
        title="Backend Engineer",
        must_have=("Python", "message queues"),
        nice_to_have=("Kafka",),
        key_terms=("event-driven", "message queues"),
    )
    verifier, fake = make(SUPPORTED, entry_id=None, facts=(QUEUE,), posting=posting)
    verifier.check("Helped move order processing to a message queue.")
    prompt = fake.requests[0]["messages"][0]["content"]
    assert "<boundaries>\n- Did not own the deployment pipeline.\n</boundaries>" in prompt, (
        "the boundaries of the facts' role"
    )
    assert "<gaps>\n- Rust\n- Swift\n</gaps>" in prompt
    assert (
        "<posting_terms>\n- event-driven\n- message queues\n- Python\n- Kafka\n</posting_terms>"
    ) in prompt


def test_text_about_a_project_has_no_role_boundaries() -> None:
    verifier, fake = make(SUPPORTED, entry_id="recipe_box", facts=(RECIPES,))
    verifier.check("Built a recipe-sharing web app with full-text search for 300 people.")
    assert "<boundaries>\nnone\n</boundaries>" in fake.requests[0]["messages"][0]["content"]


def test_the_text_is_checked_and_stored_in_ascii() -> None:
    verifier, fake = make(SUPPORTED)
    report = verifier.check(f"  {GOOD[:-1]} — before the morning reports.  ")
    assert report.text == f"{GOOD[:-1]} - before the morning reports."
    assert fake.requests[0]["messages"][0]["content"].endswith(f"<text>\n{report.text}\n</text>")


def test_there_must_be_text_and_a_known_entry() -> None:
    verifier, _ = make()
    with pytest.raises(ValueError, match="no text to check"):
        verifier.check("  ")
    with pytest.raises(ValueError, match="no role, education entry or project 'fabrikam'"):
        make(entry_id="fabrikam")


# --- Writing with one rewrite (FR-FID-7) --------------------------------------------------------


class Writer:
    """Hands out scripted texts and records the feedback it gets."""

    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.feedback: list[str | None] = []

    def __call__(self, feedback: str | None) -> str:
        self.feedback.append(feedback)
        return self.texts.pop(0)


def test_text_that_passes_is_written_once() -> None:
    verifier, _ = make(SUPPORTED)
    writer = Writer(GOOD)
    report = verifier.write(writer)
    assert (report.passed, report.attempts, report.text) == (True, 1, GOOD)
    assert writer.feedback == [None]


def test_text_that_fails_is_written_again_with_the_errors_as_feedback() -> None:
    verifier, _ = make(added_tool("Kafka"), SUPPORTED)
    writer = Writer("Reduced the nightly export job from 50 to 10 minutes using Kafka.", GOOD)
    report = verifier.write(writer)
    assert (report.passed, report.attempts, report.text) == (True, 2, GOOD)
    assert writer.feedback == [
        None,
        "- '50 to 10 minutes' isn't in the cited facts\n"
        '- "Kafka" names a tool the cited facts don\'t. On "using Kafka": No fact names Kafka.',
    ]


def test_text_that_fails_twice_is_flagged() -> None:
    verifier, _ = make(added_tool("Kafka"), added_tool("Kafka"))
    writer = Writer("Reduced the export using Kafka.", "Reduced the export with Kafka.")
    report = verifier.write(writer)
    assert (report.passed, report.attempts) == (False, 2)
    assert report.text == "Reduced the export with Kafka."
    assert ("claims", "error", "added_tool") in found(report)


def test_errors_a_rewrite_cant_fix_are_flagged_without_one() -> None:
    verifier, _ = make(refusal())
    writer = Writer(GOOD, GOOD)
    report = verifier.write(writer)
    assert (report.passed, report.attempts) == (False, 1)
    assert found(report) == [("claims", "error", "claims_unchecked")]
    assert writer.feedback == [None]
