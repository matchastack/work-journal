from pathlib import Path

import pytest

from app.review import describe, needs, report_lines
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
from app.schema.verification import Derivation, VerifierFinding, VerifierReport

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
ROLE = Role(id="example_co", name="Example Co", position="Data Engineer", start_date="2026-10")


@pytest.mark.parametrize(
    ("op", "lines"),
    [
        (
            AddBullet(parent_id="northwind", bullet=Bullet(id="nw_new", text="Added alerts.")),
            ["New bullet under northwind (Engineer at Northwind Traders):", "  Added alerts."],
        ),
        (
            EditBullet(bullet_id="nw_export", text="Cut it."),
            [
                "New version of bullet nw_export:",
                "  - Cut the nightly export job from 50 to 12 minutes by batching database writes.",
                "  + Cut it.",
            ],
        ),
        (
            SetBulletStatus(bullet_id="nw_tracker", status="active"),
            ["Make bullet nw_tracker active"],
        ),
        (
            SetBulletStatus(bullet_id="nw_export", status="benched", reason="Too old."),
            ["Make bullet nw_export benched (Too old.)"],
        ),
        (
            AddSwap(bullet_id="nw_export", swap=Swap(text="Sped up the export.")),
            ["New phrasing for bullet nw_export:", "  Sped up the export."],
        ),
        (AddRole(role=ROLE), ["New role example_co: Data Engineer at Example Co, 2026-10 to now"]),
        (
            AddEducation(
                education=Education(
                    id="cert",
                    institution="Online",
                    study_type="Certificate",
                    area="Cloud",
                    start_date="2026-01",
                )
            ),
            ["New education entry cert: Certificate, Online"],
        ),
        (
            AddProject(project=Project(id="cli", name="CLI Tool", keywords=("Python",))),
            ["New project cli: CLI Tool (Python)"],
        ),
        (
            UpdateField(target_id="northwind", field="endDate", value="2026-09"),
            ["Set endDate of northwind to 2026-09"],
        ),
        (
            AddSkill(skill=Skill(name="dbt", category="Data", verify_before_shipping=True)),
            ["New skill dbt in Data, marked verify-before-shipping"],
        ),
        (
            ResolveOpenQuestion(question_id="q_nw_volume"),
            [
                "Mark open question q_nw_volume answered: How many order events a day do the "
                "services handle?"
            ],
        ),
    ],
)
def test_each_operation_is_described_in_words(op: ChangeOp, lines: list[str]) -> None:
    assert describe(op, PROFILE) == lines


def test_a_change_needs_the_new_item_it_goes_under() -> None:
    project = AddProject(project=Project(id="cli", name="CLI Tool"))
    ops: list[ChangeOp] = [
        AddRole(role=ROLE),
        project,
        AddBullet(parent_id="example_co", bullet=Bullet(id="ex_1", text="Built it.")),
        UpdateField(target_id="cli", field="status", value="active"),
        AddBullet(parent_id="northwind", bullet=Bullet(id="nw_1", text="Built it.")),
        EditBullet(bullet_id="nw_export", text="Cut it."),
    ]
    assert [needs(op, ops) for op in ops] == [None, None, 0, 1, None, None]


def test_report_lines_list_the_findings_then_the_verdict() -> None:
    flagged = VerifierReport(
        text="Cut it by 76% with Kafka.",
        passed=False,
        attempts=2,
        findings=(
            VerifierFinding(
                check="claims", severity="error", code="added_tool", message="Kafka is added"
            ),
        ),
        derivations=(Derivation(quantity="76%", formula="|12 - 50| / 50 = 76"),),
    )
    assert report_lines(flagged) == [
        "error    claims   Kafka is added",
        "Derived 76% from the facts: |12 - 50| / 50 = 76",
        "Flagged: it didn't pass the checks, so don't use it as it is.",
    ]
    retried = VerifierReport(text="Cut it.", passed=True, attempts=2)
    assert report_lines(retried) == ["Passed the checks when written the second time."]
