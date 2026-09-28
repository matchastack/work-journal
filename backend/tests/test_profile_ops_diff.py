import json
from pathlib import Path

import pytest

from app.profile_ops import Change, apply, diff, format_changes
from app.schema.changes import (
    AddBullet,
    AddSkill,
    ChangeOp,
    EditBullet,
    ResolveOpenQuestion,
    SetBulletStatus,
    UpdateField,
)
from app.schema.profile import Bullet, Profile, Skill

FIXTURE = Path(__file__).parent / "fixtures" / "profile.json"


@pytest.fixture
def profile() -> Profile:
    return Profile.model_validate(json.loads(FIXTURE.read_text()))


def test_identical_profiles_have_no_changes(profile: Profile) -> None:
    assert diff(profile, profile.model_copy(deep=True)) == []


def test_diff_shows_exactly_the_applied_changes(profile: Profile) -> None:
    ops: list[ChangeOp] = [
        UpdateField(target_id="basics", field="label", value="Backend Engineer"),
        EditBullet(
            bullet_id="nw_export", text="Cut the export from 50 to 12 min.", fact_ids=("f3",)
        ),
        AddBullet(parent_id="northwind", bullet=Bullet(id="nw_new", text="Added retries.")),
        SetBulletStatus(bullet_id="ct_datasets", status="benched", reason="Weak."),
        AddSkill(skill=Skill(name="Go", category="Languages")),
        ResolveOpenQuestion(question_id="q_nw_volume", fact_ids=("f9",)),
    ]
    changes = diff(profile, apply(profile, ops))
    assert [(change.kind, change.path) for change in changes] == [
        ("changed", "basics.label"),
        ("changed", "work[northwind].highlights[nw_export].text"),
        ("changed", "work[northwind].highlights[nw_export].sources"),
        ("added", "work[northwind].highlights[nw_new]"),
        ("changed", "work[contoso].highlights[ct_datasets].status"),
        ("changed", "work[contoso].highlights[ct_datasets].statusReason"),
        ("added", "skills[Go]"),
        ("changed", "openQuestions[q_nw_volume].status"),
        ("changed", "openQuestions[q_nw_volume].answerFactIds"),
    ]
    assert changes[0] == Change("changed", "basics.label", "Software Engineer", "Backend Engineer")
    assert changes[2].after == ["fact_nw_export", "f3"]
    assert changes[3].after["text"] == "Added retries."


def test_inserted_bullet_is_one_addition_not_a_shift(profile: Profile) -> None:
    op = AddBullet(parent_id="northwind", bullet=Bullet(id="nw_top", text="Top."), position=0)
    assert [(change.kind, change.path) for change in diff(profile, apply(profile, [op]))] == [
        ("added", "work[northwind].highlights[nw_top]")
    ]


def test_removed_entries_are_reported(profile: Profile) -> None:
    trimmed = profile.model_copy(update={"projects": profile.projects[:1]})
    changes = diff(profile, trimmed)
    assert [(change.kind, change.path) for change in changes] == [
        ("removed", "projects[bird_calls]")
    ]
    assert changes[0].before["name"] == "Bird Call Classifier"


def test_format_changes_marks_each_kind() -> None:
    text = format_changes(
        [
            Change("added", "skills[Go]", after={"name": "Go"}),
            Change("removed", "projects[old]", before={"id": "old"}),
            Change("changed", "basics.label", before="A", after="B"),
            Change("changed", "basics.summary", before="x" * 100, after=None),
        ],
        width=20,
    )
    assert text.splitlines() == [
        '+ skills[Go]: {"name": "Go"}',
        '- projects[old]: {"id": "old"}',
        '~ basics.label: "A" -> "B"',
        '~ basics.summary: "xxxxxxxxxxxxxxxxxx… -> null',
    ]
