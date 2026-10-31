"""Tailoring drafts: the owner's choices shape the resume without another model call (T-045)."""

from pathlib import Path

import pytest

from app.schema.jobs import JobPosting
from app.schema.profile import Profile, SkillLine
from app.schema.verification import VerifierReport
from app.tailoring.drafts import (
    ChoiceError,
    DraftChoices,
    Tailoring,
    approve,
    decide_skill,
    draft_resume,
    first_choices,
    proposal,
    use_words,
)
from app.tailoring.tailor import (
    Phrasing,
    PlannedBullet,
    PlannedItem,
    PlannedSkillLine,
    TailoringPlan,
)

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
POSTING = JobPosting.model_validate_json(
    (FIXTURES / "postings" / "backend.json").read_text(encoding="utf-8")
)
ORDERS = "Build and maintain Python services that process order events from a message queue."
EXPORT = "Cut the nightly export job from 50 to 12 minutes by batching database writes."
SWAPPED = "Built event-driven Python services that process order events from a message queue."
REPHRASED = "Cut the nightly export job from 50 to 12 minutes by batching PostgreSQL writes."
PLAN = TailoringPlan(
    role_type="backend",
    work=(
        PlannedItem(
            id="northwind",
            bullets=(
                PlannedBullet(id="nw_orders", relevance="high", swap=1),
                PlannedBullet(id="nw_export", relevance="high", rephrase=True),
            ),
        ),
    ),
    skills=(PlannedSkillLine(label="Languages", skills=("Python", "Kotlin", "SQL")),),
)


def phrasing(bullet_id: str, original: str, text: str) -> Phrasing:
    report = VerifierReport(text=text, passed=True, fact_ids=("fact_nw_export",))
    return Phrasing(bullet_id=bullet_id, original=original, report=report)


TAILORING = Tailoring(
    plan=PLAN,
    swaps=(phrasing("nw_orders", ORDERS, SWAPPED),),
    proposals=(phrasing("nw_export", EXPORT, REPHRASED),),
    notes=("nw_tracker has none of its facts here, so it keeps its own words",),
)


def texts(choices: DraftChoices) -> dict[str, str]:
    resume = draft_resume(PROFILE, POSTING, TAILORING, choices)
    return {bullet.id: bullet.text for bullet in resume.selection.work[0].bullets}


def test_a_new_draft_uses_its_swaps_and_waits_for_approval_of_new_phrasings() -> None:
    choices = first_choices(TAILORING)
    resume = draft_resume(PROFILE, POSTING, TAILORING, choices)
    assert texts(choices) == {"nw_orders": SWAPPED, "nw_export": EXPORT}
    assert [phrasing.bullet_id for phrasing in resume.used] == ["nw_orders"]
    assert resume.role_type == "backend"
    assert resume.notes == TAILORING.notes
    assert resume.selection.max_pages == 1
    orders, export = resume.bullets
    assert (orders.id, orders.under, orders.use, orders.original) == (
        "nw_orders",
        "Northwind Traders",
        "swap",
        ORDERS,
    )
    assert orders.swap == TAILORING.swaps[0] and orders.phrasing is None
    assert (export.use, export.approved, export.phrasing) == ("own", False, TAILORING.proposals[0])


def test_an_approved_phrasing_is_used_and_the_owner_can_choose_any_words() -> None:
    choices = approve(first_choices(TAILORING), "nw_export")
    assert texts(choices) == {"nw_orders": SWAPPED, "nw_export": REPHRASED}
    assert approve(choices, "nw_export") == choices, "approving twice changes nothing"
    choices = use_words(choices, TAILORING, "nw_orders", "own")
    choices = use_words(choices, TAILORING, "nw_export", "own")
    assert texts(choices) == {"nw_orders": ORDERS, "nw_export": EXPORT}
    choices = use_words(choices, TAILORING, "nw_export", "phrasing")
    resume = draft_resume(PROFILE, POSTING, TAILORING, choices)
    assert [phrasing.bullet_id for phrasing in resume.used] == ["nw_export"]


def test_words_a_bullet_doesnt_have_cant_be_chosen() -> None:
    choices = first_choices(TAILORING)
    with pytest.raises(ChoiceError, match="approve the new phrasing of nw_export"):
        use_words(choices, TAILORING, "nw_export", "phrasing")
    with pytest.raises(ChoiceError, match="no swap was worked into nw_export"):
        use_words(choices, TAILORING, "nw_export", "swap")
    with pytest.raises(ChoiceError, match="nw_tracker has no new words"):
        use_words(choices, TAILORING, "nw_tracker", "own")
    with pytest.raises(ChoiceError, match="no new phrasing of nw_orders"):
        proposal(TAILORING, "nw_orders")
    assert proposal(TAILORING, "nw_export") == TAILORING.proposals[0]


def test_a_skill_marked_verify_before_shipping_waits_for_a_decision() -> None:
    """R3: the draft lists it, so the owner sees it; the rules report it until it's confirmed."""
    resume = draft_resume(PROFILE, POSTING, TAILORING, first_choices(TAILORING))
    assert resume.selection.skills == (
        SkillLine(label="Languages", skills=("Python", "Kotlin", "SQL")),
    )
    [kotlin] = resume.skills
    assert (kotlin.name, kotlin.decision, kotlin.locations) == (
        "Kotlin",
        None,
        ("skills[Languages]",),
    )
    assert resume.undecided == ("Kotlin",)
    assert [(finding.rule, finding.severity) for finding in resume.rules.errors] == [
        ("R3", "error")
    ]


def test_a_confirmed_skill_is_listed_and_passes_the_rules() -> None:
    choices = decide_skill(first_choices(TAILORING), PROFILE, "kotlin", "confirmed")
    assert choices.skills == {"Kotlin": "confirmed"}, "kept under the catalogue's name"
    resume = draft_resume(PROFILE, POSTING, TAILORING, choices)
    assert resume.selection.skills[0].skills == ("Python", "Kotlin", "SQL")
    assert resume.undecided == ()
    assert resume.rules.ok


def test_a_dropped_skill_leaves_the_skills_lines() -> None:
    choices = decide_skill(first_choices(TAILORING), PROFILE, "Kotlin", "dropped")
    resume = draft_resume(PROFILE, POSTING, TAILORING, choices)
    assert resume.selection.skills == (SkillLine(label="Languages", skills=("Python", "SQL")),)
    [kotlin] = resume.skills
    assert (kotlin.decision, kotlin.locations) == ("dropped", ()), "still shown, to undo"
    assert resume.undecided == ()
    assert resume.rules.ok
    assert not any("Kotlin" in note for note in resume.notes), "dropping isn't a note"
    undone = decide_skill(choices, PROFILE, "Kotlin", None)
    assert undone.skills == {}
    assert draft_resume(PROFILE, POSTING, TAILORING, undone).undecided == ("Kotlin",)


def test_text_that_names_a_dropped_skill_still_breaks_r3() -> None:
    kotlin = "Cut the nightly export job from 50 to 12 minutes with Kotlin."
    tailoring = TAILORING.model_copy(update={"proposals": (phrasing("nw_export", EXPORT, kotlin),)})
    choices = decide_skill(
        approve(first_choices(tailoring), "nw_export"), PROFILE, "Kotlin", "dropped"
    )
    resume = draft_resume(PROFILE, POSTING, tailoring, choices)
    [skill] = resume.skills
    assert skill.locations == ("work[northwind].highlights[nw_export]",)
    assert resume.undecided == ()
    assert [finding.location for finding in resume.rules.errors] == [
        "work[northwind].highlights[nw_export]"
    ], "the resume can be downloaded, but mustn't be sent (R2)"


def test_only_skills_marked_verify_before_shipping_take_a_decision() -> None:
    with pytest.raises(ChoiceError, match="Python isn't a skill marked verify-before-shipping"):
        decide_skill(first_choices(TAILORING), PROFILE, "Python", "confirmed")


def test_the_same_choices_give_the_same_resume() -> None:
    choices = approve(first_choices(TAILORING), "nw_export")
    stored = DraftChoices.model_validate_json(choices.model_dump_json())
    kept = Tailoring.model_validate_json(TAILORING.model_dump_json())
    assert draft_resume(PROFILE, POSTING, kept, stored) == draft_resume(
        PROFILE, POSTING, TAILORING, choices
    )
