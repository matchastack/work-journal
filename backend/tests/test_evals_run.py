from datetime import UTC, date, datetime
from pathlib import Path

import httpx2
import pytest
from anthropic import APIStatusError
from anthropic.types.beta import BetaMessage
from pydantic import TypeAdapter

from app.config import Settings
from app.evals.dataset import (
    AdversarialCase,
    Case,
    Dataset,
    ExpectedFact,
    ExtractionCase,
    Suite,
    WritingCase,
)
from app.evals.run import CaseResult, Evaluation, SuiteResult, cost_usd, run_suite
from app.extraction import ExtractedFact, ExtractedMetric, ExtractionReply
from app.llm.client import LLMClient, LLMConfigError
from app.llm.fake import FakeMessages, reply
from app.llm.usage import CallRecord, MemoryCallLog
from app.schema.fact import Fact
from app.schema.profile import Profile
from app.validate.claims import ClaimReply, JudgedClaim
from app.writing import WrittenBullet

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
FACTS = {
    fact.id: fact
    for fact in TypeAdapter(tuple[Fact, ...]).validate_json(
        (FIXTURES / "facts.json").read_text(encoding="utf-8")
    )
}
NOTE = "finally got the nightly export down from ~50 min to 12!! batching the db writes did it."
EXPORT = ExtractedFact(
    statement="Cut the nightly export from about 50 to 12 minutes by batching database writes.",
    kind="accomplishment",
    metrics=(
        ExtractedMetric(
            subject="nightly export duration",
            kind="change",
            numbers=(50, 12),
            unit="minutes",
            qualifier="approximately",
        ),
    ),
    ownership="owned",
    role_id="northwind",
)
EXPECTED = ExpectedFact(terms=("export", "batch"), numbers=(50, 12), role="northwind")
GOOD = "Cut the nightly export job from 50 to 12 minutes by batching PostgreSQL writes."
QUEUE = "Led the move of order processing from a cron job to a message queue."


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def run(suite: Suite, *cases: Case, replies: list[BetaMessage | Exception]) -> SuiteResult:
    settings = Settings(llm_model_standard="standard-model", llm_price_standard=(3, 15))
    log = MemoryCallLog()
    client = LLMClient(settings, FakeMessages(*replies), log)
    dataset = Dataset(suite, Path(f"{suite}.toml"), PROFILE, FACTS, cases)
    return run_suite(dataset, client, log)


def extract(case: ExtractionCase, *facts: ExtractedFact) -> CaseResult:
    return run("extraction", case, replies=[reply(ExtractionReply(facts=facts))]).cases[0]


def note_case(**fields: object) -> ExtractionCase:
    values: dict[str, object] = {
        "id": "export",
        "note": NOTE,
        "written": date(2026, 9, 14),
        "expected": [EXPECTED],
    }
    return ExtractionCase.model_validate(values | fields)


def judged(*claims: JudgedClaim) -> BetaMessage:
    return reply(ClaimReply(claims=claims))


def supported(claim: str) -> BetaMessage:
    return judged(JudgedClaim(claim=claim, reason="The facts say so.", verdict="supported"))


def unsupported(claim: str, issue: str) -> BetaMessage:
    return judged(
        JudgedClaim.model_validate(
            {"claim": claim, "reason": "The facts don't say so.", "verdict": "unsupported"}
            | {"issue": issue}
        )
    )


def written(text: str) -> BetaMessage:
    return reply(WrittenBullet(text=text))


def api_error(status: int, error_type: str) -> APIStatusError:
    body = {"type": "error", "error": {"type": error_type, "message": "Something went wrong."}}
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request, json=body)
    return APIStatusError("Something went wrong.", response=response, body=body)


# --- Extraction ------------------------------------------------------------------------------


def test_a_fact_that_matches_the_expected_one_is_recovered() -> None:
    result = extract(note_case(), EXPORT)
    assert result == CaseResult(
        case_id="export",
        passed=True,
        expected_facts=1,
        recovered_facts=1,
        calls=result.calls,
    )
    assert [call.task for call in result.calls] == ["fact_extraction"]


def test_a_number_the_note_doesnt_give_is_altered_and_its_fact_is_lost() -> None:
    invented = EXPORT.model_copy(
        update={"metrics": (EXPORT.metrics[0].model_copy(update={"numbers": (50, 8)}),)}
    )
    result = extract(note_case(), invented)
    assert (result.passed, result.altered_numbers, result.recovered_facts) == (False, 1, 0)
    assert result.notes == ("missed expected fact 1", "1 number the note doesn't give")


def test_a_fact_that_claims_more_than_the_note_is_one_inflated_claim() -> None:
    expected = EXPECTED.model_copy(update={"ownership": "contributed"})
    case = note_case(expected=[expected], never=[r"\bspark\b", r"\bkafka\b"])
    inflated = EXPORT.model_copy(update={"tools": ("Kafka",)})
    result = extract(case, inflated)
    assert (result.passed, result.inflated_claims, result.recovered_facts) == (False, 1, 1)
    assert result.notes == (
        "a fact claims more than the note: a tool the note doesn't name, never pattern 2, "
        "ownership above contributed (expected fact 1)",
    )


def test_a_tool_the_note_names_by_an_alias_is_not_inflated() -> None:
    case = note_case(note=NOTE + " wrote the batcher in nodejs")
    result = extract(case, EXPORT.model_copy(update={"tools": ("Node.js",)}))
    assert (result.passed, result.inflated_claims) == (True, 0)


def test_facts_from_a_note_that_gives_none_fail_the_case() -> None:
    chit_chat = note_case(id="chit chat", note="made dumplings", expected=[])
    assert extract(chit_chat).passed
    result = extract(chit_chat, ExtractedFact(statement="Made dumplings.", kind="learning"))
    assert not result.passed
    assert result.notes == ("1 fact where none were expected",)


def test_extra_facts_are_noted_without_failing_the_case() -> None:
    extra = ExtractedFact(statement="Batched the database writes.", kind="accomplishment")
    result = extract(note_case(), extra, EXPORT)
    assert (result.passed, result.recovered_facts) == (True, 1)
    assert result.notes == ("1 extra fact",)


def test_each_expected_fact_needs_a_fact_of_its_own() -> None:
    result = extract(note_case(expected=[EXPECTED, EXPECTED]), EXPORT)
    assert (result.expected_facts, result.recovered_facts) == (2, 1)
    assert result.notes == ("missed expected fact 2",)


def test_the_owners_choice_of_role_links_the_facts() -> None:
    expected = EXPECTED.model_copy(update={"role": "contoso"})
    result = extract(note_case(role="contoso", expected=[expected]), EXPORT)
    assert result.recovered_facts == 1


# --- Writing ---------------------------------------------------------------------------------


def test_a_bullet_that_passes_and_keeps_its_numbers_passes() -> None:
    case = WritingCase(id="numbers", facts=("fact_nw_export",), keeps=(r"\b50\b", r"\b12\b"))
    result = run("writing", case, replies=[written(GOOD), supported(GOOD)]).cases[0]
    assert (result.passed, result.altered_numbers, result.inflated_claims) == (True, 0, 0)
    assert result.notes == ()
    assert [call.task for call in result.calls] == ["bullet_writing", "claim_verification"]


def test_a_never_match_is_an_inflated_claim_even_when_the_verifier_passes_it() -> None:
    text = "Owned the cut of the nightly export job from 50 to 12 minutes."
    case = WritingCase(id="modest", facts=("fact_nw_export",), never=(r"^(Led|Owned)\b",))
    result = run("writing", case, replies=[written(text), supported(text)]).cases[0]
    assert (result.passed, result.inflated_claims) == (False, 1)
    assert result.notes == ("never pattern 1 matched",)


def test_a_bullet_still_flagged_after_its_rewrite_counts_its_claims_and_numbers() -> None:
    text = "Led the cut of the nightly export job from 50 to 8 minutes."
    case = WritingCase(id="flagged", facts=("fact_nw_export",), keeps=(r"\b50\b", r"\b12\b"))
    flagged = unsupported("Led the cut", "inflated_ownership")
    replies: list[BetaMessage | Exception] = [written(text), flagged, written(text), flagged]
    result = run("writing", case, replies=replies).cases[0]
    assert (result.passed, result.altered_numbers, result.inflated_claims) == (False, 1, 1)
    assert result.notes == (
        "written twice",
        "flagged: inflated_ownership, unsupported_number",
        "keeps pattern 2 missing",
    )


# --- Adversarial -----------------------------------------------------------------------------


def known_bad(*catch: str, text: str = QUEUE, fact: str = "fact_nw_queue") -> AdversarialCase:
    return AdversarialCase(id="bad", text=text, facts=(fact,), catch=catch)


def test_known_bad_text_caught_with_its_code_passes() -> None:
    reply_ = unsupported("Led the move", "inflated_ownership")
    result = run("adversarial", known_bad("inflated_ownership"), replies=[reply_]).cases[0]
    assert (result.passed, result.inflated_claims) == (True, 0)
    assert result.notes == ("caught: inflated_ownership",)


def test_known_bad_text_that_gets_through_is_an_inflated_claim() -> None:
    result = run("adversarial", known_bad("inflated_ownership"), replies=[supported(QUEUE)])
    case = result.cases[0]
    assert (case.passed, case.inflated_claims, case.altered_numbers) == (False, 1, 0)
    assert case.notes == ("missed: it passed",)


def test_known_bad_text_caught_for_another_reason_is_still_a_miss() -> None:
    reply_ = unsupported("a message queue", "added_scope")
    case = run("adversarial", known_bad("inflated_ownership"), replies=[reply_]).cases[0]
    assert (case.passed, case.inflated_claims) == (False, 1)
    assert case.notes == ("missed: added_scope",)


def test_a_wrong_number_that_gets_through_is_an_altered_number() -> None:
    bad = known_bad("unit_changed", text=GOOD, fact="fact_nw_export")
    case = run("adversarial", bad, replies=[supported(GOOD)]).cases[0]
    assert (case.passed, case.altered_numbers, case.inflated_claims) == (False, 1, 0)


def test_faithful_text_the_verifier_fails_is_a_false_alarm() -> None:
    faithful = AdversarialCase(id="faithful", text=GOOD, facts=("fact_nw_export",))
    assert run("adversarial", faithful, replies=[supported(GOOD)]).cases[0].passed
    reply_ = unsupported("PostgreSQL", "added_tool")
    case = run("adversarial", faithful, replies=[reply_]).cases[0]
    assert (case.passed, case.altered_numbers, case.inflated_claims) == (False, 0, 0)
    assert case.notes == ("false alarm: added_tool",)


def test_the_posting_terms_reach_the_claim_check() -> None:
    settings = Settings(llm_model_standard="standard-model")
    fake, log = FakeMessages(supported(GOOD)), MemoryCallLog()
    case = AdversarialCase(
        id="posting", text=GOOD, facts=("fact_nw_export",), posting_terms=("relational databases",)
    )
    dataset = Dataset("adversarial", Path("adversarial.toml"), PROFILE, FACTS, (case,))
    run_suite(dataset, LLMClient(settings, fake, log), log)
    assert "- relational databases" in fake.requests[0]["messages"][0]["content"]


# --- Running a suite -------------------------------------------------------------------------


def test_a_case_whose_call_fails_is_reported_and_the_run_goes_on() -> None:
    faithful = AdversarialCase(id="faithful", text=GOOD, facts=("fact_nw_export",))
    unavailable = faithful.model_copy(update={"id": "unavailable"})
    result = run(
        "adversarial",
        unavailable,
        faithful,
        replies=[api_error(529, "overloaded_error"), supported(GOOD)],
    )
    failed, passed = result.cases
    assert failed.error == "the Claude API is unavailable: APIStatusError (529)"
    assert [call.outcome for call in failed.calls] == ["unavailable"]
    assert passed.passed
    assert (result.passed, result.errors) == (1, 1)


def test_each_case_has_its_own_calls_and_their_cost() -> None:
    case = WritingCase(id="numbers", facts=("fact_nw_export",))
    result = run(
        "writing",
        case,
        case.model_copy(update={"id": "again"}),
        replies=[written(GOOD), supported(GOOD), written(GOOD), supported(GOOD)],
    )
    assert [len(case.calls) for case in result.cases] == [2, 2]
    # Each fake reply has 100 input and 20 output tokens, at $3 and $15 per million.
    assert cost_usd(result.cases[0].calls) == pytest.approx(2 * (300 + 300) / 1_000_000)
    assert cost_usd(result.calls) == pytest.approx(4 * (300 + 300) / 1_000_000)


def test_a_missing_setting_stops_the_run() -> None:
    log = MemoryCallLog()
    client = LLMClient(Settings(), FakeMessages(), log)
    dataset = Dataset("extraction", Path("extraction.toml"), PROFILE, {}, (note_case(),))
    with pytest.raises(LLMConfigError):
        run_suite(dataset, client, log)


def test_the_progress_hears_about_each_case() -> None:
    settings = Settings(llm_model_standard="standard-model")
    log = MemoryCallLog()
    client = LLMClient(settings, FakeMessages(reply(ExtractionReply(facts=(EXPORT,)))), log)
    dataset = Dataset("extraction", Path("extraction.toml"), PROFILE, {}, (note_case(),))
    heard: list[str] = []
    run_suite(dataset, client, log, progress=lambda case: heard.append(case.case_id))
    assert heard == ["export"]


# --- The bar ---------------------------------------------------------------------------------


def evaluation(*cases: CaseResult) -> Evaluation:
    return Evaluation(suites=(SuiteResult(suite="extraction", cases=cases),))


def test_the_bar_needs_90_percent_recall() -> None:
    assert evaluation(CaseResult("a", True, expected_facts=10, recovered_facts=9)).meets_bar
    assert not evaluation(CaseResult("a", False, expected_facts=9, recovered_facts=8)).meets_bar


@pytest.mark.parametrize(
    "case",
    [
        CaseResult("a", False, altered_numbers=1),
        CaseResult("a", False, inflated_claims=1),
        CaseResult("a", False, error="the Claude API is unavailable"),
    ],
)
def test_the_bar_allows_no_altered_number_inflated_claim_or_error(case: CaseResult) -> None:
    assert not evaluation(case).meets_bar


def test_the_bar_holds_without_expected_facts() -> None:
    result = evaluation(CaseResult("a", True))
    assert result.recall is None
    assert result.meets_bar


def test_the_cost_is_unknown_when_a_tier_has_no_price() -> None:
    priced = CallRecord(
        at=datetime(2026, 10, 13, tzinfo=UTC),
        task="fact_extraction",
        tier="standard",
        model="standard-model",
        prompt="fact_extraction/v1",
        outcome="ok",
        attempts=1,
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.0006,
        latency_ms=900,
    )
    assert cost_usd([priced, priced]) == pytest.approx(0.0012)
    assert cost_usd([priced, priced.model_copy(update={"cost_usd": None})]) is None
    assert cost_usd([]) == 0
