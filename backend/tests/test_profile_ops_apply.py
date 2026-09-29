import json
from pathlib import Path

import pytest

from app.profile_ops import ProfileOpError, apply
from app.schema.changes import (
    AddBullet,
    AddEducation,
    AddProject,
    AddRole,
    AddSkill,
    AddSwap,
    ChangeOp,
    EditBullet,
    ResolveOpenQuestion,
    SetBulletStatus,
    UpdateField,
)
from app.schema.profile import Bullet, Education, Profile, Project, Role, Skill, Swap

FIXTURE = Path(__file__).parent / "fixtures" / "profile.json"


@pytest.fixture
def profile() -> Profile:
    return Profile.model_validate(json.loads(FIXTURE.read_text()))


def bullet_by_id(profile: Profile, bullet_id: str) -> Bullet:
    return next(bullet for bullet in profile.all_bullets() if bullet.id == bullet_id)


def test_no_ops_returns_an_equal_profile(profile: Profile) -> None:
    assert apply(profile, []) == profile


def test_add_bullet_appends_and_records_sources(profile: Profile) -> None:
    op = AddBullet(
        parent_id="northwind",
        bullet=Bullet(id="nw_retries", text="Added retries to the order consumer."),
        fact_ids=("f1",),
    )
    result = apply(profile, [op])
    highlights = result.work[0].highlights
    assert highlights[-1].id == "nw_retries"
    assert highlights[-1].sources == ("f1",)
    assert len(highlights) == len(profile.work[0].highlights) + 1


def test_add_bullet_at_a_position(profile: Profile) -> None:
    op = AddBullet(parent_id="northwind", bullet=Bullet(id="nw_first", text="First."), position=0)
    assert apply(profile, [op]).work[0].highlights[0].id == "nw_first"


def test_add_bullet_position_past_the_end_is_an_error(profile: Profile) -> None:
    op = AddBullet(parent_id="northwind", bullet=Bullet(id="nw_x", text="X."), position=99)
    with pytest.raises(ProfileOpError, match="past the end"):
        apply(profile, [op])


def test_add_bullet_works_for_education_and_projects(profile: Profile) -> None:
    ops: list[ChangeOp] = [
        AddBullet(parent_id="springfield_state", bullet=Bullet(id="ss_award", text="Dean's list")),
        AddBullet(parent_id="recipe_box", bullet=Bullet(id="rb_tests", text="Added tests.")),
    ]
    result = apply(profile, ops)
    assert result.education[0].highlights[-1].id == "ss_award"
    assert result.projects[0].highlights[-1].id == "rb_tests"


def test_edit_bullet_changes_text_and_merges_sources(profile: Profile) -> None:
    op = EditBullet(bullet_id="nw_export", text="New wording.", fact_ids=("f2", "fact_nw_export"))
    edited = bullet_by_id(apply(profile, [op]), "nw_export")
    assert edited.text == "New wording."
    assert edited.sources == ("fact_nw_export", "f2")


def test_bench_then_reactivate_a_bullet(profile: Profile) -> None:
    benched = apply(
        profile, [SetBulletStatus(bullet_id="nw_export", status="benched", reason="Old.")]
    )
    assert bullet_by_id(benched, "nw_export").status == "benched"
    assert bullet_by_id(benched, "nw_export").status_reason == "Old."
    active = apply(benched, [SetBulletStatus(bullet_id="nw_export", status="active")])
    assert bullet_by_id(active, "nw_export").status == "active"
    assert bullet_by_id(active, "nw_export").status_reason is None


def test_add_swap(profile: Profile) -> None:
    op = AddSwap(bullet_id="nw_export", swap=Swap(text="Sped up the nightly export 4x."))
    assert bullet_by_id(apply(profile, [op]), "nw_export").swaps[-1].text.startswith("Sped up")


def test_add_role_education_and_project(profile: Profile) -> None:
    ops: list[ChangeOp] = [
        AddRole(
            role=Role(id="fabrikam", name="Fabrikam", position="Engineer", start_date="2027-01")
        ),
        AddEducation(
            education=Education(
                id="cert",
                institution="Online",
                study_type="Certificate",
                area="Cloud",
                start_date="2026-01",
            )
        ),
        AddProject(project=Project(id="cli_tool", name="CLI Tool")),
    ]
    result = apply(profile, ops)
    assert result.work[-1].id == "fabrikam"
    assert result.education[-1].id == "cert"
    assert result.projects[-1].id == "cli_tool"


def test_update_field_on_a_role_a_bullet_and_basics(profile: Profile) -> None:
    ops: list[ChangeOp] = [
        UpdateField(target_id="northwind", field="endDate", value="2027-06"),
        UpdateField(target_id="nw_export", field="priority", value=1),
        UpdateField(target_id="basics", field="label", value="Backend Engineer"),
    ]
    result = apply(profile, ops)
    assert result.work[0].end_date == "2027-06"
    assert bullet_by_id(result, "nw_export").priority == 1
    assert result.basics.label == "Backend Engineer"


@pytest.mark.parametrize("field", ["id", "highlights", "salary", "end_date"])
def test_update_field_rejects_protected_or_unknown_fields(profile: Profile, field: str) -> None:
    with pytest.raises(ProfileOpError, match="no editable field"):
        apply(profile, [UpdateField(target_id="northwind", field=field, value="x")])


def test_update_field_value_must_keep_the_profile_valid(profile: Profile) -> None:
    with pytest.raises(ProfileOpError, match="invalid"):
        apply(profile, [UpdateField(target_id="northwind", field="endDate", value="Sep 2027")])


def test_add_skill_and_gap_conflicts(profile: Profile) -> None:
    result = apply(profile, [AddSkill(skill=Skill(name="Go", category="Languages"))])
    assert result.skills[-1].name == "Go"
    with pytest.raises(ProfileOpError, match="invalid"):
        apply(profile, [AddSkill(skill=Skill(name="Rust", category="Languages"))])


def test_resolve_open_question_records_answer_facts(profile: Profile) -> None:
    op = ResolveOpenQuestion(question_id="q_nw_volume", fact_ids=("f9",))
    question = apply(profile, [op]).open_questions[0]
    assert question.status == "answered"
    assert question.answer_fact_ids == ("f9",)


@pytest.mark.parametrize(
    ("op", "message"),
    [
        (AddBullet(parent_id="nope", bullet=Bullet(id="b", text="x")), "no role, education entry"),
        (EditBullet(bullet_id="nope", text="x"), "no bullet has ID 'nope'"),
        (SetBulletStatus(bullet_id="nope", status="active"), "no bullet has ID 'nope'"),
        (AddSwap(bullet_id="nope", swap=Swap(text="x")), "no bullet has ID 'nope'"),
        (UpdateField(target_id="nope", field="text", value="x"), "nothing in the profile has ID"),
        (ResolveOpenQuestion(question_id="nope"), "no open question has ID 'nope'"),
    ],
)
def test_unknown_ids_raise_clear_errors(profile: Profile, op: ChangeOp, message: str) -> None:
    with pytest.raises(ProfileOpError, match=message):
        apply(profile, [op])


def test_error_names_the_failing_operation(profile: Profile) -> None:
    ops: list[ChangeOp] = [
        EditBullet(bullet_id="nw_export", text="Fine."),
        EditBullet(bullet_id="missing", text="Fails."),
    ]
    with pytest.raises(ProfileOpError, match=r"operation 2 \(edit_bullet\)"):
        apply(profile, ops)


def test_apply_is_atomic_and_never_modifies_the_input(profile: Profile) -> None:
    original = profile.model_copy(deep=True)
    ops: list[ChangeOp] = [
        EditBullet(bullet_id="nw_export", text="Changed."),
        AddRole(role=Role(id="northwind", name="Dup", position="X", start_date="2027-01")),
    ]
    with pytest.raises(ProfileOpError, match="unique across the profile"):
        apply(profile, ops)
    assert profile == original
    apply(profile, ops[:1])
    assert profile == original
