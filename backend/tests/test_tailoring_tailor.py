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
from app.render.fit import cut_order
from app.schema.fact import Fact
from app.schema.jobs import JobPosting, SwapUse
from app.schema.profile import Profile, SkillLine
from app.tailoring import tailor as tailoring
from app.tailoring.tailor import (
    PlannedBullet,
    PlannedCourses,
    PlannedItem,
    PlannedSkillLine,
    PlannedTitle,
    Tailored,
    TailoringPlan,
    posting_spellings,
    tailor,
    tailoring_context,
    use_phrasings,
)
from app.validate.claims import ClaimReply, JudgedClaim
from app.validate.lint import check_sendable
from app.writing import WrittenBullet

FIXTURES = Path(__file__).parent / "fixtures"
POSTING = JobPosting.model_validate_json(
    (FIXTURES / "postings" / "backend.json").read_text(encoding="utf-8")
)
FACTS = TypeAdapter(tuple[Fact, ...]).validate_json(
    (FIXTURES / "facts.json").read_text(encoding="utf-8")
)
ORDERS = Fact(
    id="fact_nw_orders",
    statement="Built and maintained Python services that process order events from a message "
    "queue.",
    ownership="owned",
    role_id="northwind",
)


def fixture_profile() -> Profile:
    """The fictional profile, with the facts its order-service bullet was written from."""
    data = json.loads((FIXTURES / "profile.json").read_text(encoding="utf-8"))
    data["work"][0]["highlights"][0]["sources"] = ["fact_nw_orders"]
    data["work"][0]["highlights"][1]["swaps"] = [
        {"text": "I cut our export job to 12 minutes.", "contexts": ["LinkedIn"]}
    ]
    return Profile.model_validate(data)


PROFILE = fixture_profile()
SUPPORTED = ClaimReply(
    claims=(
        JudgedClaim(
            claim="the work",
            reason="The facts say so.",
            verdict="supported",
            fact_ids=("fact_nw_export",),
        ),
    )
)
ADDED_KAFKA = ClaimReply(
    claims=(
        JudgedClaim(
            claim="on Kafka",
            reason="No fact names Kafka.",
            verdict="unsupported",
            issue="added_tool",
            term="Kafka",
        ),
    )
)
SWAPPED = "Built event-driven Python services that process order events from a message queue."
REPHRASED = "Cut the nightly export job from 50 to 12 minutes by batching PostgreSQL writes."
PLAN = TailoringPlan(
    role_type="backend",
    work=(
        PlannedItem(
            id="northwind",
            bullets=(
                PlannedBullet(id="nw_export", relevance="high"),
                PlannedBullet(id="nw_orders", relevance="low"),
            ),
        ),
    ),
    projects=(PlannedItem(id="recipe_box", bullets=(PlannedBullet(id="rb_app"),)),),
    titles=(PlannedTitle(role_id="northwind", title="Backend Engineer"),),
    courses=(
        PlannedCourses(
            education_id="springfield_state",
            courses=("Distributed Systems", "Databases", "Algorithms", "Operating Systems"),
        ),
    ),
    skills=(
        PlannedSkillLine(label="Languages", skills=("Python", "SQL")),
        PlannedSkillLine(label="Databases", skills=("PostgreSQL",)),
    ),
)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def run(
    plan: TailoringPlan,
    *replies: BetaMessage | ClaimReply,
    posting: JobPosting = POSTING,
    **options: Any,
) -> tuple[Tailored, FakeMessages]:
    """Tailor with scripted replies: the plan, then the writer's and the claim check's."""
    scripted = [reply(plan), *(r if isinstance(r, BetaMessage) else reply(r) for r in replies)]
    fake = FakeMessages(*scripted)
    settings = Settings(llm_model_heavy="heavy-model", llm_model_standard="standard-model")
    client = LLMClient(settings, fake, MemoryCallLog())
    options = {"facts": (*FACTS, ORDERS), **options}
    return tailor(PROFILE, posting, client, **options), fake


def written(text: str) -> BetaMessage:
    return reply(WrittenBullet(text=text))


def bullets(tailored: Tailored) -> list[tuple[str, str, int]]:
    selection = tailored.selection
    return [
        (item.id, bullet.id, bullet.priority)
        for item in (*selection.work, *selection.projects)
        for bullet in item.bullets
    ]


def plan(**changes: Any) -> TailoringPlan:
    return PLAN.model_copy(update=changes)


# --- The plan ----------------------------------------------------------------------------------


def test_the_heavy_model_plans_from_the_profile_and_the_posting() -> None:
    tailored, fake = run(PLAN)
    request = fake.requests[0]
    assert request["model"] == "heavy-model"
    assert request["system"][0]["text"].startswith("You choose what goes on a one-page resume")
    assert request["system"][1]["text"] == tailoring_context(PROFILE), "cached with the prompt"
    posting = json.loads(request["messages"][0]["content"].split("<posting>\n")[1].split("\n</")[0])
    assert posting["title"] == "Senior Backend Engineer, Payments"
    assert "url" not in posting
    assert len(fake.requests) == 1, "no swap or new phrasing was asked for"
    assert tailored.role_type == "backend"
    assert tailored.notes == ()


def test_the_context_holds_what_a_resume_may_show() -> None:
    context = tailoring_context(PROFILE)
    data = json.loads(context.split("\n", 1)[1])
    northwind, contoso = data["roles"]
    assert northwind["loadBearing"] is True
    assert northwind["titleVariants"] == ["Software Engineer", "Backend Engineer"]
    assert contoso["cutFor"] == ["backend"]
    orders, export = northwind["bullets"]
    assert orders["swaps"] == [
        {
            "number": 1,
            "text": "event-driven Python services",
            "replaces": "Python services",
            "contexts": ["platform roles"],
        }
    ]
    assert export["swaps"] == [], "a swap for LinkedIn never goes on a resume"
    assert [b["id"] for b in northwind["bullets"]] == ["nw_orders", "nw_export"], "not benched"
    assert [project["id"] for project in data["projects"]] == ["recipe_box"], "not planned"
    assert data["education"][0]["courseSubsets"]["data"] == [
        "Databases",
        "Statistics",
        "Algorithms",
    ]
    kotlin = next(skill for skill in data["skills"] if skill["name"] == "Kotlin")
    assert kotlin["verifyBeforeShipping"] is True
    assert data["gaps"] == ["Rust", "Swift"]
    for private in ("Casey Morgan", "casey@example.com", "555"):
        assert private not in context


def test_the_plan_becomes_a_one_page_selection() -> None:
    tailored, _ = run(PLAN)
    selection = tailored.selection
    assert selection.max_pages == 1
    assert selection.sections == ("education", "work", "projects", "skills")
    assert [role.id for role in selection.work] == ["northwind"], "contoso adds nothing here"
    assert selection.work[0].title == "Backend Engineer"
    assert bullets(tailored) == [
        ("northwind", "nw_export", 1),
        ("northwind", "nw_orders", 3),
        ("recipe_box", "rb_app", 2),
    ], "most relevant first, with the relevance as priority"
    assert selection.education[0].courses == (
        "Distributed Systems",
        "Databases",
        "Algorithms",
        "Operating Systems",
    )
    assert selection.skills == (
        SkillLine(label="Languages", skills=("Python", "SQL")),
        SkillLine(label="Databases", skills=("PostgreSQL",)),
    )
    assert selection.summary is None


def test_the_selection_passes_the_resume_rules() -> None:
    posting = POSTING.model_copy(update={"must_have": ("NodeJS",)})
    chosen = plan(skills=(PlannedSkillLine(label="Backend & APIs", skills=("Node.js",)),))
    tailored, _ = run(chosen, posting=posting)
    report = check_sendable(
        tailored.selection, PROFILE, posting_spellings=posting_spellings(posting)
    )
    assert report.errors == ()
    assert [finding.rule for finding in report.warnings] == [], "NodeJS is the posting's spelling"


def test_fitting_cuts_the_least_relevant_bullets_first() -> None:
    tailored, _ = run(PLAN)
    order = cut_order(tailored.selection)
    work = tailored.selection.work[0]
    assert work.bullets[order[0].bullet].id == "nw_orders"


def test_load_bearing_roles_and_items_kept_for_the_role_type_always_appear() -> None:
    tailored, _ = run(plan(role_type="data", work=(), projects=(), titles=()))
    assert [role.id for role in tailored.selection.work] == ["northwind", "contoso"]
    assert [b.id for b in tailored.selection.work[1].bullets] == ["ct_sensors", "ct_datasets"]
    assert tailored.selection.projects == ()
    tailored, _ = run(plan(projects=()))
    assert [project.id for project in tailored.selection.projects] == ["recipe_box"]


def test_what_the_profile_doesnt_allow_is_left_out_with_a_note() -> None:
    chosen = plan(
        work=(
            PlannedItem(id="fabrikam"),
            PlannedItem(id="contoso", bullets=(PlannedBullet(id="ct_sensors"),)),
            PlannedItem(
                id="northwind",
                bullets=(PlannedBullet(id="nw_tracker"), PlannedBullet(id="rb_app")),
            ),
        ),
        projects=(PlannedItem(id="bird_calls"),),
        titles=(PlannedTitle(role_id="northwind", title="Senior Backend Engineer"),),
    )
    tailored, _ = run(chosen)
    assert [role.id for role in tailored.selection.work] == ["northwind"]
    assert [b.id for b in tailored.selection.work[0].bullets] == ["nw_orders", "nw_export"], (
        "with no valid bullet chosen, the role shows its own"
    )
    assert tailored.selection.work[0].title == "Software Engineer", "its first approved title"
    assert [p.id for p in tailored.selection.projects] == ["recipe_box"], "kept for backend"
    assert tailored.notes == (
        '"Senior Backend Engineer" isn\'t an approved title for northwind',
        "there's no role fabrikam, so it's left out",
        "contoso is cut for this role type or not active, so it's left out",
        "nw_tracker isn't an active bullet of northwind, so it's left out",
        "rb_app isn't an active bullet of northwind, so it's left out",
        "bird_calls is cut for this role type or not active, so it's left out",
    )


def test_an_unknown_role_type_and_a_missing_summary_are_noted() -> None:
    tailored, _ = run(plan(role_type="mobile", summary=True))
    assert tailored.role_type is None
    assert tailored.selection.summary is None
    assert tailored.notes == (
        "mobile isn't one of your role types, so none is used",
        "there's no summary for this role type, so none is shown",
    )


def test_the_summary_and_skills_can_lead() -> None:
    tailored, _ = run(plan(summary=True, skills_first=True))
    selection = tailored.selection
    assert selection.summary == "Backend engineer building event-driven Python services."
    assert selection.sections == ("summary", "education", "skills", "work", "projects")


# --- Courses and skills ------------------------------------------------------------------------


def test_too_few_courses_are_topped_up_from_the_role_types_subset() -> None:
    chosen = plan(
        courses=(PlannedCourses(education_id="springfield_state", courses=("Statistics", "Art")),)
    )
    tailored, _ = run(chosen)
    assert tailored.selection.education[0].courses == (
        "Statistics",
        "Algorithms",
        "Operating Systems",
        "Distributed Systems",
    )
    assert tailored.notes == (
        "springfield_state lists courses that aren't in its coursework, so they're left out",
        "springfield_state needs 4 courses, so the list was topped up",
    )


def test_without_courses_in_the_plan_the_role_types_subset_is_topped_up_quietly() -> None:
    tailored, _ = run(plan(courses=()))
    assert tailored.selection.education[0].courses == (
        "Algorithms",
        "Operating Systems",
        "Distributed Systems",
        "Databases",
    )
    assert tailored.notes == ()


def test_at_most_six_courses_are_listed() -> None:
    data = PROFILE.model_dump(mode="json", by_alias=True)
    data["education"][0]["courses"] += ["Compilers", "Networks", "Security"]
    profile = Profile.model_validate(data)
    courses = tuple(profile.education[0].courses)
    chosen = plan(courses=(PlannedCourses(education_id="springfield_state", courses=courses),))
    fake = FakeMessages(reply(chosen))
    client = LLMClient(Settings(llm_model_heavy="heavy-model"), fake, MemoryCallLog())
    tailored = tailor(profile, POSTING, client)
    assert tailored.selection.education[0].courses == courses[:6]
    assert tailored.notes == ("springfield_state lists 6 of the 8 courses chosen",)


def test_skills_are_catalogue_skills_in_the_postings_spelling() -> None:
    posting = POSTING.model_copy(update={"must_have": ("NodeJS", "python", "Kafka")})
    chosen = plan(
        skills=(
            PlannedSkillLine(label="Languages", skills=("Python", "Rust", "Kotlin")),
            PlannedSkillLine(label="Streaming", skills=("Kafka", "Node.js", "python")),
        )
    )
    tailored, _ = run(chosen, posting=posting)
    assert tailored.selection.skills == (
        SkillLine(label="Languages", skills=("python",)),
        SkillLine(label="Backend & APIs", skills=("NodeJS",)),
    ), "an unknown label becomes the skill's category"
    assert tailored.notes == (
        "Rust is on your gaps list, so it's never listed",
        "Kotlin is marked verify-before-shipping, so it's listed only once you confirm it for "
        "this resume",
        "Kafka isn't in your skills catalogue, so it's left out",
    )
    assert posting_spellings(posting)[:3] == ("NodeJS", "python", "Kafka")


def test_a_confirmed_skill_is_listed() -> None:
    chosen = plan(skills=(PlannedSkillLine(label="Languages", skills=("Python", "Kotlin")),))
    tailored, _ = run(chosen, confirmed=["kotlin"])
    assert tailored.selection.skills == (SkillLine(label="Languages", skills=("Python", "Kotlin")),)


def test_without_skills_in_the_plan_the_role_types_preset_is_used() -> None:
    tailored, _ = run(plan(skills=()))
    assert [line.label for line in tailored.selection.skills] == [
        "Languages",
        "Backend & APIs",
        "Databases",
    ]


# --- Swaps and new phrasings -------------------------------------------------------------------


def with_bullets(*planned: PlannedBullet) -> TailoringPlan:
    return plan(work=(PlannedItem(id="northwind", bullets=planned),))


def test_a_chosen_swap_is_worked_in_and_verified() -> None:
    chosen = with_bullets(PlannedBullet(id="nw_orders", swap=1))
    tailored, fake = run(chosen, written(SWAPPED), SUPPORTED)
    assert tailored.selection.work[0].bullets[0].text == SWAPPED
    assert tailored.swaps_used == (SwapUse(bullet_id="nw_orders", text=SWAPPED),)
    assert tailored.proposals == ()
    message = fake.requests[1]["messages"][0]["content"]
    assert "<swap>\nevent-driven Python services\n</swap>" in message
    facts = json.loads(message.split("<facts>\n")[1].split("\n</facts>")[0])
    assert [fact["id"] for fact in facts] == ["fact_nw_orders"], "the bullet's own facts"
    assert "<posting_terms>\n- APIs" in fake.requests[2]["messages"][0]["content"]


def test_a_swap_that_fails_twice_is_not_used() -> None:
    chosen = with_bullets(PlannedBullet(id="nw_orders", swap=1))
    kafka = "Built event-driven Python services on Kafka."
    replies = (written(kafka), ADDED_KAFKA, written(kafka), ADDED_KAFKA)
    tailored, _ = run(chosen, *replies)
    assert tailored.selection.work[0].bullets[0].text.startswith("Build and maintain Python")
    assert tailored.swaps == ()
    assert tailored.notes == ("the swap for nw_orders didn't pass the checks, so it isn't used",)


def test_a_new_phrasing_is_offered_for_approval() -> None:
    chosen = with_bullets(PlannedBullet(id="nw_export", rephrase=True))
    tailored, fake = run(chosen, written(REPHRASED), SUPPORTED)
    original = "Cut the nightly export job from 50 to 12 minutes by batching database writes."
    assert tailored.selection.work[0].bullets[0].text == original, "not before it's approved"
    [proposal] = tailored.proposals
    assert (proposal.bullet_id, proposal.original, proposal.text) == (
        "nw_export",
        original,
        REPHRASED,
    )
    assert fake.requests[1]["system"][0]["text"].startswith(
        "You write one bullet for a resume tailored to a job posting"
    )
    assert use_phrasings(tailored, {"nw_export"}).work[0].bullets[0].text == REPHRASED
    assert use_phrasings(tailored, set()).work[0].bullets[0].text == original


def test_a_new_phrasing_that_fails_is_not_offered() -> None:
    chosen = with_bullets(PlannedBullet(id="nw_export", rephrase=True))
    kafka = "Cut the nightly export job from 50 to 12 minutes with Kafka."
    tailored, _ = run(chosen, written(kafka), ADDED_KAFKA, written(kafka), ADDED_KAFKA)
    assert tailored.proposals == ()
    assert tailored.notes == ("the new phrasing of nw_export didn't pass the checks",)


def test_rewording_needs_the_bullets_facts_and_a_usable_swap() -> None:
    chosen = with_bullets(
        PlannedBullet(id="nw_orders", swap=2),
        PlannedBullet(id="nw_export", swap=1),
    )
    tailored, fake = run(chosen, facts=FACTS)
    assert len(fake.requests) == 1, "nothing is written"
    assert tailored.notes == (
        "nw_orders has none of its facts here, so it keeps its own words",
        "swap 1 of nw_export is for LinkedIn, so it isn't used",
    )
    tailored, _ = run(chosen)
    assert tailored.notes[0] == "nw_orders has no swap 2, so it keeps its own words"


def test_new_phrasings_are_limited_per_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tailoring, "MAX_NEW_PHRASINGS", 1)
    chosen = with_bullets(
        PlannedBullet(id="nw_export", rephrase=True),
        PlannedBullet(id="nw_orders", rephrase=True),
    )
    tailored, fake = run(chosen, written(REPHRASED), SUPPORTED)
    assert [proposal.bullet_id for proposal in tailored.proposals] == ["nw_export"]
    assert len(fake.requests) == 3
    assert tailored.notes == (
        "nw_orders keeps its own words: new phrasings are limited to 1 per resume",
    )
