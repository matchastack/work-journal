"""What each profile version changed, in the owner's terms (FR-PRF-2, FR-PRF-3)."""

from pathlib import Path

from app.inbox.describe import DiffPart
from app.profile.history import VersionChange, describe_path, version_changes
from app.profile_ops import apply
from app.schema.changes import (
    AddBullet,
    AddSkill,
    EditBullet,
    ResolveOpenQuestion,
    SetBulletStatus,
    UpdateField,
)
from app.schema.profile import Bullet, Profile, Skill

PROFILE = Profile.model_validate_json(
    (Path(__file__).parent / "fixtures" / "profile.json").read_text(encoding="utf-8")
)
EXPORT = "Cut the nightly export job from 50 to 12 minutes by batching database writes."
NORTHWIND = "Engineer, Northwind Traders"


def test_the_first_version_changed_nothing() -> None:
    assert version_changes(None, PROFILE) == ()
    assert version_changes(PROFILE, PROFILE) == ()


def test_reworded_text_is_compared_a_word_at_a_time() -> None:
    reworded = "Cut the nightly export from 50 to 12 minutes by batching database writes."
    after = apply(PROFILE, [EditBullet(bullet_id="nw_export", text=reworded)])
    assert version_changes(PROFILE, after) == (
        VersionChange(
            kind="changed",
            path="work[northwind].highlights[nw_export].text",
            title=f"Text of a bullet under {NORTHWIND}",
            context=None,
            before=EXPORT,
            after=reworded,
            diff=(
                DiffPart(kind="same", text="Cut the nightly export "),
                DiffPart(kind="removed", text="job "),
                DiffPart(kind="same", text="from 50 to 12 minutes by batching database writes."),
            ),
        ),
    )


def test_a_change_inside_a_bullet_names_the_bullet() -> None:
    op = SetBulletStatus(bullet_id="nw_export", status="benched", reason="Older work.")
    status, reason = version_changes(PROFILE, apply(PROFILE, [op]))
    assert (status.title, status.context) == (f"Status of a bullet under {NORTHWIND}", EXPORT)
    assert (status.before, status.after, status.diff) == ("active", "benched", ())
    assert (reason.title, reason.before, reason.after) == (
        f"Status reason of a bullet under {NORTHWIND}",
        None,
        "Older work.",
    )


def test_an_added_bullet_and_a_removed_project() -> None:
    added = AddBullet(
        parent_id="northwind", bullet=Bullet(id="nw_runbook", text="Wrote a runbook.")
    )
    after = apply(PROFILE, [added])
    after = after.model_copy(update={"projects": after.projects[:1]})
    bullet, project = version_changes(PROFILE, after)
    assert (bullet.kind, bullet.title, bullet.context) == (
        "added",
        f"Bullet added under {NORTHWIND}",
        None,
    )
    assert (bullet.before, bullet.after) == (None, "Wrote a runbook.")
    assert (project.kind, project.title) == ("removed", "Project removed: Bird Call Classifier")
    assert (project.before, project.after) == ("Bird Call Classifier", None)


def test_basics_items_and_their_fields_are_named() -> None:
    ops = [
        UpdateField(target_id="basics", field="label", value="Backend Engineer"),
        UpdateField(target_id="northwind", field="endDate", value="2026-09"),
        UpdateField(target_id="northwind", field="name", value="Northwind Trading"),
        UpdateField(target_id="springfield_state", field="studyType", value="Bachelor of Arts"),
        UpdateField(target_id="recipe_box", field="keywords", value=["TypeScript", "Vue"]),
    ]
    changes = version_changes(PROFILE, apply(PROFILE, ops))
    assert [(change.title, change.before, change.after) for change in changes] == [
        ("Label in your basics", "Software Engineer", "Backend Engineer"),
        ("Organisation of Engineer, Northwind Trading", "Northwind Traders", "Northwind Trading"),
        ("End date of Engineer, Northwind Trading", None, "2026-09"),
        (
            "Degree of Bachelor of Arts in Computer Science, Springfield State University",
            "Bachelor of Science",
            "Bachelor of Arts",
        ),
        ("Tech stack of Recipe Box", "TypeScript, React, PostgreSQL", "TypeScript, Vue"),
    ]
    assert changes[1].diff == (
        DiffPart(kind="same", text="Northwind "),
        DiffPart(kind="removed", text="Traders"),
        DiffPart(kind="added", text="Trading"),
    )


def test_tags_are_joined_and_cleared_lists_say_none() -> None:
    ops = [
        UpdateField(target_id="nw_orders", field="tags", value=[]),
        UpdateField(target_id="nw_export", field="tags", value=["backend", "data"]),
    ]
    cleared, added = version_changes(PROFILE, apply(PROFILE, ops))
    assert (cleared.before, cleared.after) == ("backend", "none")
    assert (added.before, added.after) == ("none", "backend, data")


def test_facts_are_counted_rather_than_listed() -> None:
    op = EditBullet(bullet_id="nw_export", text=EXPORT, fact_ids=("f_new",))
    (sources,) = version_changes(PROFILE, apply(PROFILE, [op]))
    assert (sources.title, sources.before, sources.after) == (
        f"Facts of a bullet under {NORTHWIND}",
        "1 fact",
        "2 facts",
    )
    question = ResolveOpenQuestion(question_id="q_nw_volume", fact_ids=("f_volume",))
    status, answers = version_changes(PROFILE, apply(PROFILE, [question]))
    assert status.title == "openQuestions[q_nw_volume].status"
    assert (answers.before, answers.after) == ("0 facts", "1 fact")


def test_other_sections_keep_their_paths() -> None:
    op = AddSkill(skill=Skill(name="Go", category="Languages"))
    (skill,) = version_changes(PROFILE, apply(PROFILE, [op]))
    assert (skill.title, skill.after) == ("Added: skills[Go]", "Go")


def test_paths_read_as_the_owner_would_say_them() -> None:
    assert describe_path("work[northwind].highlights[nw_export]", PROFILE) == (
        f"A bullet under {NORTHWIND}"
    )
    assert describe_path("projects[recipe_box]", PROFILE) == "Recipe Box"
    assert describe_path("projects[recipe_box].keywords", PROFILE) == "Tech stack of Recipe Box"
    assert describe_path("work[northwind].titleVariants", PROFILE) == (
        f"Title variants of {NORTHWIND}"
    )
    assert describe_path("basics.profiles[GitHub]", PROFILE) == "GitHub profile in your basics"
    assert describe_path("basics.location.countryCode", PROFILE) == (
        "Country code of location in your basics"
    )
    assert describe_path("education[springfield_state].courseworkSubsets[backend]", PROFILE) == (
        "Backend coursework of Bachelor of Science in Computer Science, "
        "Springfield State University"
    )
    assert describe_path("work[gone].highlights[x].text", PROFILE) == "Text of a bullet under gone"


def test_paths_it_doesnt_know_stay_as_they_are() -> None:
    for path in (
        "summaries[backend]",
        "skillPresets[backend].lines[Backend & APIs]",
        "skills[Node.js].aliases",
        "work",
        "work[northwind",
        "",
    ):
        assert describe_path(path, PROFILE) == path
