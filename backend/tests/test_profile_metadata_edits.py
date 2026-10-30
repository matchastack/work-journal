"""The owner's edits of the metadata that drives tailoring, as change operations (T-043)."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.profile.edits import (
    CourseworkEdit,
    Edit,
    InvalidEdit,
    MissingTarget,
    OpenQuestionEdit,
    RoleRulesEdit,
    RoleTypesEdit,
    SkillPresetsEdit,
    SkillsEdit,
    SummariesEdit,
    SwapsEdit,
    coursework_changes,
    new_question_changes,
    question_changes,
    role_rules_changes,
    role_types_changes,
    skill_presets_changes,
    skills_changes,
    summaries_changes,
    swaps_changes,
)
from app.profile_ops import apply
from app.schema.changes import UpdateField
from app.schema.common import Note
from app.schema.profile import Profile, Skill

PROFILE = Profile.model_validate_json(
    (Path(__file__).parent / "fixtures" / "profile.json").read_text(encoding="utf-8")
)
NORTHWIND, CONTOSO = PROFILE.work
SPRINGFIELD = PROFILE.education[0]


def sent[E: Edit](model: type[E], **fields: Any) -> E:
    """An edit of the version the page showed, version 1."""
    return model.model_validate({"base_version": 1, **fields})


def dump(value: Any) -> Any:
    return [entry.model_dump(mode="json", by_alias=False) for entry in value]


def rules(**changes: Any) -> RoleRulesEdit:
    sent = NORTHWIND.model_dump(
        mode="json", by_alias=False, include=set(RoleRulesEdit.model_fields)
    )
    return RoleRulesEdit.model_validate({**sent, "base_version": 1, **changes})


def skills(**changes: Any) -> SkillsEdit:
    sent = [
        {
            **skill.model_dump(mode="json", by_alias=False, exclude={"notes"}),
            "previous_name": skill.name,
        }
        for skill in PROFILE.skills
    ]
    return SkillsEdit.model_validate(
        {"base_version": 1, "skills": sent, "skill_gaps": list(PROFILE.skill_gaps), **changes}
    )


def question(**changes: Any) -> OpenQuestionEdit:
    sent = {"question": "How many order events a day do the services handle?", "status": "open"}
    return OpenQuestionEdit.model_validate(
        {"base_version": 1, **sent, "bullet_ids": ["nw_orders"], **changes}
    )


def test_edits_that_change_nothing_make_no_operations() -> None:
    assert role_rules_changes(PROFILE, "northwind", rules()).ops == ()
    swaps = sent(SwapsEdit, swaps=dump(NORTHWIND.highlights[0].swaps))
    assert swaps_changes(PROFILE, "nw_orders", swaps).ops == ()
    coursework = CourseworkEdit.model_validate(
        {
            "base_version": 1,
            "courses": SPRINGFIELD.courses,
            "coursework_subsets": dump(SPRINGFIELD.coursework_subsets),
            "rules": SPRINGFIELD.rules,
        }
    )
    assert coursework_changes(PROFILE, "springfield_state", coursework).ops == ()
    role_types = sent(RoleTypesEdit, role_types=dump(PROFILE.role_types))
    assert role_types_changes(PROFILE, role_types).ops == ()
    assert skills_changes(PROFILE, skills()).ops == ()
    presets = sent(SkillPresetsEdit, skill_presets=dump(PROFILE.skill_presets))
    assert skill_presets_changes(PROFILE, presets).ops == ()
    summaries = sent(SummariesEdit, summaries=dump(PROFILE.summaries))
    assert summaries_changes(PROFILE, summaries).ops == ()
    assert question_changes(PROFILE, "q_nw_volume", question()).ops == ()


def test_a_roles_tailoring_rules_are_field_updates_that_apply() -> None:
    edit = rules(
        title_variants=["Software Engineer", "Platform Engineer"],
        load_bearing=False,
        cut_for=["data"],
        keep_for=["backend"],
        honesty_boundaries=[],
    )
    edited = role_rules_changes(PROFILE, "northwind", edit)
    assert edited.summary == "Edited the tailoring rules of the role Engineer, Northwind Traders"
    assert {op.field for op in edited.ops if isinstance(op, UpdateField)} == {
        "titleVariants",
        "loadBearing",
        "keepFor",
        "cutFor",
        "honestyBoundaries",
    }
    role = apply(PROFILE, edited.ops).work[0]
    assert role.title_variants == ("Software Engineer", "Platform Engineer")
    assert (role.load_bearing, role.keep_for, role.cut_for) == (False, ("backend",), ("data",))
    assert role.honesty_boundaries == ()


def test_a_role_type_is_kept_or_cut_not_both() -> None:
    with pytest.raises(ValidationError, match="both kept and cut: backend"):
        rules(keep_for=["backend"], cut_for=["backend"])


def test_tailoring_rules_name_your_role_types() -> None:
    with pytest.raises(InvalidEdit, match="mobile isn't one of your role types"):
        role_rules_changes(PROFILE, "northwind", rules(keep_for=["backend", "mobile"]))


def test_a_role_type_the_role_already_uses_can_stay() -> None:
    """An imported rule naming a role type the profile doesn't define doesn't block a save."""
    role = NORTHWIND.model_copy(update={"keep_for": ("backend", "legacy")})
    profile = PROFILE.model_copy(update={"work": (role, CONTOSO)})
    edit = rules(keep_for=["backend", "legacy"], load_bearing=False)
    assert len(role_rules_changes(profile, "northwind", edit).ops) == 1


def test_a_new_swap_is_saved_whole() -> None:
    swap = {
        "text": "high-volume order services",
        "replaces": " ",
        "contexts": [],
        "origin": "approved",
    }
    current = dump(NORTHWIND.highlights[0].swaps)
    edited = swaps_changes(PROFILE, "nw_orders", sent(SwapsEdit, swaps=[*current, swap]))
    assert edited.summary == "Edited the swaps of a bullet under Engineer, Northwind Traders"
    bullet = apply(PROFILE, edited.ops).work[0].highlights[0]
    assert [(s.text, s.replaces, s.origin) for s in bullet.swaps] == [
        ("event-driven Python services", "Python services", "imported"),
        ("high-volume order services", None, "approved"),
    ]


def test_coursework_subsets_come_from_the_full_list() -> None:
    edit = CourseworkEdit.model_validate(
        {
            "base_version": 1,
            "courses": ["Algorithms", "Databases"],
            "coursework_subsets": [
                {"role_type": "backend", "courses": ["Algorithms", "Compilers"]}
            ],
            "rules": [],
        }
    )
    with pytest.raises(InvalidEdit, match="backend coursework lists Compilers"):
        coursework_changes(PROFILE, "springfield_state", edit)


def test_coursework_and_rules_apply() -> None:
    edit = CourseworkEdit.model_validate(
        {
            "base_version": 1,
            "courses": ["Algorithms", "Databases"],
            "coursework_subsets": [{"role_type": "data", "courses": ["Databases"]}],
            "rules": ["Never print the GPA."],
        }
    )
    edited = coursework_changes(PROFILE, "springfield_state", edit)
    education = apply(PROFILE, edited.ops).education[0]
    assert education.courses == ("Algorithms", "Databases")
    assert [(s.role_type, s.courses) for s in education.coursework_subsets] == [
        ("data", ("Databases",))
    ]
    assert education.rules == ("Never print the GPA.",)
    assert edited.summary.startswith("Edited the coursework and rules of Bachelor of Science")


def test_a_role_type_in_use_cant_be_removed() -> None:
    edit = sent(RoleTypesEdit, role_types=dump(PROFILE.role_types[:1]))
    with pytest.raises(InvalidEdit, match="data is still used by the data skills preset"):
        role_types_changes(PROFILE, edit)


def test_role_types_are_added_and_renamed() -> None:
    renamed = [{"id": "backend", "name": "Backend"}, *dump(PROFILE.role_types[1:])]
    edit = sent(RoleTypesEdit, role_types=[*renamed, {"id": "ml", "name": "ML"}])
    edited = role_types_changes(PROFILE, edit)
    assert [(t.id, t.name) for t in apply(PROFILE, edited.ops).role_types] == [
        ("backend", "Backend"),
        ("data", "Data engineering"),
        ("ml", "ML"),
    ]
    with pytest.raises(InvalidEdit, match="the role type ml is listed twice"):
        role_types_changes(
            PROFILE,
            sent(RoleTypesEdit, role_types=[*renamed, *[{"id": "ml", "name": "ML"}] * 2]),
        )


def test_skills_keep_their_notes_through_a_rename() -> None:
    noted = PROFILE.skills[0].model_copy(update={"notes": (Note(text="Main language."),)})
    profile = PROFILE.model_copy(update={"skills": (noted, *PROFILE.skills[1:])})
    sent = skills().model_dump(mode="json", by_alias=False)["skills"]
    sent[0] |= {"name": "Python 3", "tier": "working", "verify_before_shipping": True}
    sent.append({"name": "Go", "previous_name": None, "category": "Languages", "tier": None,
                 "verify_before_shipping": False, "aliases": ["Golang"]})  # fmt: skip
    edited = skills_changes(profile, skills(skills=sent))
    result = apply(profile, edited.ops).skills
    assert result[0] == Skill(
        name="Python 3",
        category="Languages",
        tier="working",
        verify_before_shipping=True,
        notes=(Note(text="Main language."),),
    )
    assert result[-1] == Skill(name="Go", category="Languages", aliases=("Golang",))
    assert edited.summary == "Edited your skills"


def test_a_gap_is_never_also_a_skill() -> None:
    with pytest.raises(InvalidEdit, match="python can't be both a skill and a gap"):
        skills_changes(PROFILE, skills(skill_gaps=["Rust", "python"]))
    with pytest.raises(InvalidEdit, match="the gap rust is listed twice"):
        skills_changes(PROFILE, skills(skill_gaps=["Rust", "rust"]))


def test_the_gaps_list_is_one_update() -> None:
    edited = skills_changes(PROFILE, skills(skill_gaps=["Rust", "Swift", "Haskell"]))
    assert edited.ops == (
        UpdateField(target_id="profile", field="skillGaps", value=["Rust", "Swift", "Haskell"]),
    )


def test_presets_and_summaries_are_one_per_role_type() -> None:
    preset = {"role_type": "data", "lines": [{"label": "Languages", "skills": ["Python", "SQL"]}]}
    edited = skill_presets_changes(PROFILE, sent(SkillPresetsEdit, skill_presets=[preset]))
    assert [p.role_type for p in apply(PROFILE, edited.ops).skill_presets] == ["data"]
    with pytest.raises(InvalidEdit, match="a skills preset for data is listed twice"):
        skill_presets_changes(PROFILE, sent(SkillPresetsEdit, skill_presets=[preset] * 2))
    summary = {"role_type": "mobile", "text": "Ships apps."}
    with pytest.raises(InvalidEdit, match="mobile isn't one of your role types"):
        summaries_changes(PROFILE, sent(SummariesEdit, summaries=[summary]))


def test_a_new_open_question_gets_an_id_and_blocks_its_bullets() -> None:
    edit = question(question="How long did the export take before?", bullet_ids=["nw_export"])
    edited = new_question_changes(PROFILE, edit)
    assert edited.summary == "Added an open question"
    result = apply(PROFILE, edited.ops)
    added = result.open_questions[-1]
    assert (added.id, added.question, added.status) == (
        "q_1",
        "How long did the export take before?",
        "open",
    )
    assert result.bullets_blocked_by("q_1")[0].id == "nw_export"


def test_answering_a_question_keeps_its_facts_and_moves_its_bullets() -> None:
    answered = PROFILE.open_questions[0].model_copy(update={"answer_fact_ids": ("f1",)})
    profile = PROFILE.model_copy(update={"open_questions": (answered, *PROFILE.open_questions[1:])})
    edited = question_changes(
        profile, "q_nw_volume", question(status="answered", bullet_ids=["nw_export"])
    )
    assert edited.summary == "Answered an open question"
    result = apply(profile, edited.ops)
    assert (result.open_questions[0].status, result.open_questions[0].answer_fact_ids) == (
        "answered",
        ("f1",),
    )
    assert [bullet.id for bullet in result.bullets_blocked_by("q_nw_volume")] == ["nw_export"]


@pytest.mark.parametrize(
    ("status", "summary"),
    [("closed", "Closed an open question"), ("open", "Edited an open question")],
)
def test_a_questions_summary_says_what_changed(status: str, summary: str) -> None:
    edit = question(status=status, question="How many order events an hour?")
    assert question_changes(PROFILE, "q_nw_volume", edit).summary == summary
    reopened = question_changes(PROFILE, "q_project_dates", question(status="open", bullet_ids=[]))
    assert reopened.summary == "Reopened an open question"


def test_a_question_and_its_bullets_must_exist() -> None:
    with pytest.raises(MissingTarget, match="no open question q_nope"):
        question_changes(PROFILE, "q_nope", question())
    with pytest.raises(InvalidEdit, match="no bullet nope"):
        new_question_changes(PROFILE, question(bullet_ids=["nope"]))
