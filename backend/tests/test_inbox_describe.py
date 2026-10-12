"""What each proposed change does, in the owner's terms (FR-REV-1)."""

from pathlib import Path

import pytest

from app.inbox.describe import DiffPart, Output, describe, word_diff
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

PROFILE = Profile.model_validate_json(
    (Path(__file__).parent / "fixtures" / "profile.json").read_text(encoding="utf-8")
)
EXPORT = "Cut the nightly export job from 50 to 12 minutes by batching database writes."
NORTHWIND = "Engineer, Northwind Traders"
EVERY: tuple[Output, ...] = ("master_resume", "tailored_resumes", "portfolio", "linkedin")
NOT_LINKEDIN: tuple[Output, ...] = ("master_resume", "tailored_resumes", "portfolio")


def test_a_reworded_bullet_shows_its_text_before_and_after() -> None:
    described = describe(EditBullet(bullet_id="nw_export", text="Cut it to 12 minutes."), PROFILE)
    assert described.title == f"Reword a bullet under {NORTHWIND}"
    assert (described.before, described.after) == (EXPORT, "Cut it to 12 minutes.")
    assert described.outputs == EVERY
    assert described.editable


@pytest.mark.parametrize(
    ("op", "title", "outputs"),
    [
        (
            AddBullet(parent_id="northwind", bullet=Bullet(id="nw_new", text="Wrote a runbook.")),
            f"Add a bullet under {NORTHWIND}",
            EVERY,
        ),
        (
            AddBullet(
                parent_id="recipe_box",
                bullet=Bullet(id="rb_new", text="Added tags.", visibility="resume_only"),
            ),
            "Add a bullet under Recipe Box",
            ("master_resume", "tailored_resumes"),
        ),
        (
            AddBullet(
                parent_id="northwind",
                bullet=Bullet(id="nw_plan", text="Will migrate the queue.", status="planned"),
            ),
            f"Add a planned bullet under {NORTHWIND}",
            (),
        ),
        (
            AddBullet(parent_id="springfield_state", bullet=Bullet(id="ss_new", text="Tutor.")),
            "Add a bullet under Bachelor of Science in Computer Science, "
            "Springfield State University",
            NOT_LINKEDIN,
        ),
    ],
)
def test_a_new_bullet_reaches_the_outputs_that_show_it(
    op: ChangeOp, title: str, outputs: tuple[Output, ...]
) -> None:
    """Benched and planned bullets show nowhere (R8); others stay off the page (FR-PRT-4)."""
    described = describe(op, PROFILE)
    assert (described.title, described.before, described.outputs) == (title, None, outputs)


def test_benching_a_bullet_takes_it_out_of_the_outputs_that_showed_it() -> None:
    bench = SetBulletStatus(bullet_id="nw_export", status="benched", reason="Too old")
    described = describe(bench, PROFILE)
    assert described.title == f"Bench a bullet under {NORTHWIND}"
    assert (described.context, described.before, described.after) == (
        EXPORT,
        "active",
        "benched: Too old",
    )
    assert described.outputs == EVERY
    assert not described.editable
    again = describe(SetBulletStatus(bullet_id="nw_tracker", status="active"), PROFILE)
    assert again.title == f"Use a bullet again under {NORTHWIND}"
    assert again.outputs == EVERY
    planned = describe(SetBulletStatus(bullet_id="bc_model", status="benched", reason="x"), PROFILE)
    assert planned.outputs == ()


def test_a_swap_is_only_for_tailored_resumes() -> None:
    swap = Swap(text="Shortened the nightly export", replaces="Cut the nightly export job")
    described = describe(AddSwap(bullet_id="nw_export", swap=swap), PROFILE)
    assert described.title == f"Add a swap for a bullet under {NORTHWIND}"
    assert (described.context, described.before, described.after) == (
        EXPORT,
        "Cut the nightly export job",
        "Shortened the nightly export",
    )
    assert described.outputs == ("tailored_resumes",)


@pytest.mark.parametrize(
    ("op", "title", "after", "outputs"),
    [
        (
            AddRole(
                role=Role(id="fabrikam", name="Fabrikam", position="Engineer", start_date="2026-09")
            ),
            "Add a role",
            "Engineer, Fabrikam (2026-09 - now)",
            EVERY,
        ),
        (
            AddEducation(
                education=Education(
                    id="cert",
                    institution="Example Institute",
                    study_type="Certificate",
                    area="Data Engineering",
                    start_date="2026-01",
                    end_date="2026-06",
                )
            ),
            "Add education",
            "Certificate in Data Engineering, Example Institute (2026-01 - 2026-06)",
            NOT_LINKEDIN,
        ),
        (
            AddProject(project=Project(id="garden", name="Garden Log", description="Plant diary")),
            "Add a project",
            "Garden Log: Plant diary",
            NOT_LINKEDIN,
        ),
        (
            AddProject(project=Project(id="later", name="Later", status="planned")),
            "Add a project",
            "Later",
            (),
        ),
        (
            AddSkill(skill=Skill(name="Go", category="Languages", tier="working")),
            "Add a skill",
            "Go (Languages, working)",
            NOT_LINKEDIN,
        ),
        (
            UpdateField(target_id="northwind", field="endDate", value="2026-09"),
            f"Change endDate of {NORTHWIND}",
            "2026-09",
            EVERY,
        ),
        (
            UpdateField(target_id="basics", field="label", value="Data Engineer"),
            "Change label of your basics",
            "Data Engineer",
            EVERY,
        ),
        (
            UpdateField(target_id="nw_export", field="tags", value=["backend", "data"]),
            f"Change tags of a bullet under {NORTHWIND}",
            '["backend", "data"]',
            EVERY,
        ),
        (
            ResolveOpenQuestion(question_id="q_nw_volume"),
            "Answer an open question",
            "answered",
            (),
        ),
    ],
)
def test_each_kind_of_change_says_what_it_does(
    op: ChangeOp, title: str, after: str, outputs: tuple[Output, ...]
) -> None:
    described = describe(op, PROFILE)
    assert (described.title, described.after, described.outputs) == (title, after, outputs)
    assert not described.editable


def test_a_change_to_something_gone_is_still_described() -> None:
    """Applying it reports the problem."""
    described = describe(EditBullet(bullet_id="gone", text="New text."), PROFILE)
    assert described.title == "Reword a bullet under gone"
    assert (described.before, described.outputs) == (None, ())


def test_word_diff_keeps_shared_words_and_marks_the_rest() -> None:
    assert word_diff(
        "Cut the export from 50 to 12 minutes.", "Cut the nightly export to 12 minutes."
    ) == (
        DiffPart(kind="same", text="Cut the "),
        DiffPart(kind="added", text="nightly "),
        DiffPart(kind="same", text="export "),
        DiffPart(kind="removed", text="from 50 "),
        DiffPart(kind="same", text="to 12 minutes."),
    )
    assert word_diff("Same text.", "Same text.") == (DiffPart(kind="same", text="Same text."),)
    assert word_diff("old", "new") == (
        DiffPart(kind="removed", text="old"),
        DiffPart(kind="added", text="new"),
    )
