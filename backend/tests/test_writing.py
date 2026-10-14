import json
from pathlib import Path
from typing import Any

import pytest
from anthropic.types.beta import BetaMessage
from pydantic import TypeAdapter

from app.config import Settings
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, reply
from app.llm.usage import MemoryCallLog
from app.schema.fact import Fact
from app.schema.jobs import JobPosting
from app.schema.profile import Profile
from app.schema.verification import VerifierReport
from app.validate.claims import ClaimReply, JudgedClaim
from app.writing import WrittenBullet, write_bullet, writing_context

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
FACTS = TypeAdapter(tuple[Fact, ...]).validate_json(
    (FIXTURES / "facts.json").read_text(encoding="utf-8")
)
EXPORT, QUEUE, RECIPES = FACTS
POSTING = JobPosting.model_validate_json(
    (FIXTURES / "postings" / "backend.json").read_text(encoding="utf-8")
)
GOOD = "Cut the nightly export job from 50 to 12 minutes by batching PostgreSQL writes."
SUPPORTED = ClaimReply(
    claims=(
        JudgedClaim(
            claim="Cut the nightly export job",
            reason="The fact says the export went from 50 to 12 minutes.",
            verdict="supported",
            fact_ids=("fact_nw_export",),
        ),
    )
)
ADDED_KAFKA = ClaimReply(
    claims=(
        JudgedClaim(
            claim="using Kafka",
            reason="No fact names Kafka.",
            verdict="unsupported",
            issue="added_tool",
            term="Kafka",
        ),
    )
)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def written(text: str) -> BetaMessage:
    return reply(WrittenBullet(text=text))


def write(
    *replies: BetaMessage | ClaimReply, facts: tuple[Fact, ...] = (EXPORT,), **options: Any
) -> tuple[VerifierReport, FakeMessages]:
    """Write a bullet with scripted replies: the writer's, then the claim check's, in turn."""
    fake = FakeMessages(*(r if isinstance(r, BetaMessage) else reply(r) for r in replies))
    client = LLMClient(Settings(llm_model_standard="standard-model"), fake, MemoryCallLog())
    options = {"entry_id": "northwind", **options}
    return write_bullet(facts, PROFILE, client, **options), fake


def user_message(fake: FakeMessages, request: int) -> str:
    return fake.requests[request]["messages"][0]["content"]


def test_facts_are_written_as_a_bullet_that_passes_the_verifier() -> None:
    report, fake = write(written(GOOD), SUPPORTED)
    assert (report.text, report.passed, report.attempts) == (GOOD, True, 1)
    assert report.fact_ids == ("fact_nw_export",)
    assert [request["model"] for request in fake.requests] == ["standard-model"] * 2
    system = fake.requests[0]["system"]
    assert system[0]["text"].startswith("You write one bullet for someone's resume")
    assert system[1]["text"] == writing_context(PROFILE), "the profile is cached context"
    message = user_message(fake, 0)
    assert message.startswith('The bullet goes under the role "northwind".')
    facts = message.split("<facts>\n")[1].split("\n</facts>")[0]
    assert [fact["id"] for fact in json.loads(facts)] == ["fact_nw_export"]
    assert "<current>\nnone\n</current>" in message
    assert "<attempt>\nnone\n</attempt>\n<problems>\nnone\n</problems>" in message
    assert user_message(fake, 1).endswith(f"<text>\n{GOOD}\n</text>"), "then the claim check"


def test_the_context_holds_what_a_bullet_may_draw_on_and_no_contact_details() -> None:
    context = writing_context(PROFILE)
    assert context.startswith("The person's profile:\n")
    data = json.loads(context.split("\n", 1)[1])
    northwind, contoso = data["roles"]
    assert northwind["organisation"] == "Northwind Traders"
    assert northwind["sensitive"] is True
    assert "sensitive" not in contoso
    assert northwind["honestyBoundaries"] == ["Did not own the deployment pipeline."]
    assert northwind["endDate"] is None
    assert len(northwind["bullets"]) == 2, "the benched bullet is left out"
    assert data["education"][0]["bullets"] == ["Minor in Mathematics"]
    assert [(p["id"], p["status"], p["bullets"]) for p in data["projects"]][1] == (
        "bird_calls",
        "planned",
        [],
    )
    assert data["skills"][:3] == ["Python", "TypeScript", "SQL"]
    assert data["gaps"] == ["Rust", "Swift"]
    for private in ("Casey Morgan", "casey@example.com", "555", "Used an issue tracker"):
        assert private not in context


def test_an_edit_is_written_from_the_current_bullet_and_replaces_it() -> None:
    current = "Cut the nightly export job from 50 to 12 minutes by batching database writes."
    report, fake = write(written(current), SUPPORTED, facts=(EXPORT, QUEUE), edit="nw_export")
    assert f"<current>\n{current}\n</current>" in user_message(fake, 0)
    assert report.passed
    codes = [finding.code for finding in report.findings]
    assert "duplicates" not in codes, "the new version replaces the bullet it repeats"


def test_a_bullet_that_fails_is_written_again_with_the_attempt_and_its_problems() -> None:
    first = "Cut the nightly export job from 50 to 10 minutes using Kafka."
    report, fake = write(written(first), ADDED_KAFKA, written(GOOD), SUPPORTED)
    assert (report.text, report.passed, report.attempts) == (GOOD, True, 2)
    retry = user_message(fake, 2)
    assert f"<attempt>\n{first}\n</attempt>" in retry
    assert (
        "<problems>\n- '50 to 10 minutes' isn't in the cited facts\n"
        '- "Kafka" names a tool the cited facts don\'t. On "using Kafka": No fact names Kafka.\n'
        "</problems>"
    ) in retry


def test_a_bullet_that_fails_twice_is_flagged() -> None:
    attempts = ("Cut the export using Kafka.", "Cut the export with Kafka.")
    report, _ = write(written(attempts[0]), ADDED_KAFKA, written(attempts[1]), ADDED_KAFKA)
    assert (report.text, report.passed, report.attempts) == (attempts[1], False, 2)
    assert "added_tool" in [finding.code for finding in report.errors]


def test_a_list_marker_before_the_bullet_is_dropped() -> None:
    report, fake = write(written(f"- {GOOD}"), SUPPORTED)
    assert report.text == GOOD
    assert user_message(fake, 1).endswith(f"<text>\n{GOOD}\n</text>")


def test_words_to_avoid_reach_the_writer_and_the_style_check() -> None:
    report, fake = write(written(GOOD), SUPPORTED, written(GOOD), SUPPORTED, avoid=("batching",))
    assert "<avoid>\n- batching\n</avoid>" in user_message(fake, 0)
    assert (report.passed, report.attempts) == (False, 2)
    assert [finding.code for finding in report.errors] == ["avoided_word"]


def test_bullets_under_projects_and_education_say_which_item() -> None:
    recipes = "Built a recipe-sharing web app with full-text search, used by 300 people a month."
    _, fake = write(written(recipes), SUPPORTED, facts=(RECIPES,), entry_id="recipe_box")
    assert user_message(fake, 0).startswith('The bullet goes under the project "recipe_box".')
    _, fake = write(written(GOOD), SUPPORTED, entry_id="springfield_state")
    assert user_message(fake, 0).startswith(
        'The bullet goes under the education entry "springfield_state".'
    )


def test_a_bullet_needs_facts_and_a_known_item_and_bullet() -> None:
    with pytest.raises(ValueError, match="at least one fact"):
        write(facts=())
    with pytest.raises(ValueError, match="no role, education entry or project 'fabrikam'"):
        write(entry_id="fabrikam")
    with pytest.raises(ValueError, match="bullet 'ct_sensors' is under 'contoso', not 'northwind'"):
        write(edit="ct_sensors")
    with pytest.raises(ValueError, match="the profile has no bullet 'nw_billing'"):
        write(edit="nw_billing")


def test_the_linkedin_style_is_written_in_the_first_person_and_checked_as_linkedin_text() -> None:
    text = "I cut our nightly export job from 50 to 12 minutes by batching PostgreSQL writes."
    report, fake = write(written(text), SUPPORTED, style="linkedin", edit="nw_export")
    assert (report.text, report.passed, report.attempts) == (text, True, 1)
    assert [finding.code for finding in report.findings] == [], "pronouns are fine on LinkedIn"
    assert fake.requests[0]["system"][0]["text"].startswith(
        "You write one short passage for someone's LinkedIn profile"
    )
    assert fake.requests[0]["system"][1]["text"] == writing_context(PROFILE)
    message = user_message(fake, 0)
    assert message.startswith('The passage goes under the role "northwind".')
    assert "<current>\nCut the nightly export job from 50 to 12 minutes" in message


def test_linkedin_text_over_the_limit_is_written_again() -> None:
    long = "I cut the nightly export job from 50 to 12 minutes. " * 40
    report, _ = write(written(long), SUPPORTED, written(long), SUPPORTED, style="linkedin")
    assert (report.passed, report.attempts) == (False, 2)
    assert [finding.code for finding in report.errors] == ["too_long"]


def test_the_posting_style_gets_the_posting_and_its_terms_are_checked() -> None:
    text = "Cut the nightly export job from 50 to 12 minutes by batching PostgreSQL writes."
    report, fake = write(written(text), SUPPORTED, style="posting", posting=POSTING)
    assert report.passed
    system = fake.requests[0]["system"][0]["text"]
    assert system.startswith("You write one bullet for a resume tailored to a job posting")
    message = user_message(fake, 0)
    posting = json.loads(message.split("<posting>\n")[1].split("\n</posting>")[0])
    assert posting["title"] == "Senior Backend Engineer, Payments"
    assert posting["mustHave"][:2] == ["Python", "Go"]
    assert "url" not in posting
    judged = user_message(fake, 1)
    assert "<posting_terms>\n- APIs\n- event-driven systems\n" in judged, (
        "the judge flags posting terms the facts don't back"
    )


def test_only_the_posting_style_takes_a_posting() -> None:
    with pytest.raises(ValueError, match="the posting style needs a job posting"):
        write(style="posting")
    with pytest.raises(ValueError, match="only it takes one"):
        write(style="linkedin", posting=POSTING)
