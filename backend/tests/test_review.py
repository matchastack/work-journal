from pathlib import Path

import pytest
from pydantic import JsonValue

from app.review import describe, needs, rechecked, report_lines
from app.schema.changes import (
    AddBullet,
    AddEducation,
    AddProject,
    AddRole,
    AddSkill,
    AddSwap,
    ChangeOp,
    ChangeSet,
    EditBullet,
    ResolveOpenQuestion,
    ReviewedOp,
    SetBulletStatus,
    UpdateField,
)
from app.schema.fact import Fact
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


# --- Checked again for what it must leave out (T-049) --------------------------------------------


def passed(text: str) -> dict[str, JsonValue]:
    return VerifierReport(text=text, passed=True).model_dump(mode="json")


def test_bullets_are_checked_again_for_confidential_terms_and_operational_detail() -> None:
    sensitive = "Moved order events to queue.payments.internal for Falcon."
    elsewhere = "Added Falcon recipes to the app."
    change_set = ChangeSet(
        author="ai",
        ops=(
            ReviewedOp(
                op=AddBullet(parent_id="northwind", bullet=Bullet(id="nw_new", text=sensitive)),
                verifier_report=passed(sensitive),
            ),
            ReviewedOp(
                op=EditBullet(bullet_id="rb_app", text=elsewhere, fact_ids=("f_recipes",)),
                verifier_report=passed(elsewhere),
            ),
            ReviewedOp(op=SetBulletStatus(bullet_id="nw_orders", status="benched", reason="Old")),
            ReviewedOp(
                op=AddBullet(parent_id="recipe_box", bullet=Bullet(id="rb_new", text="Kept it.")),
                verifier_report={"not": "a report"},
            ),
        ),
    )
    checked = rechecked(change_set, PROFILE, {}, ["falcon"])
    first, second, benched, unreadable = (
        VerifierReport.model_validate(reviewed.verifier_report) if index < 2 else reviewed
        for index, reviewed in enumerate(checked.ops)
    )
    assert isinstance(first, VerifierReport) and isinstance(second, VerifierReport)
    assert not first.passed
    assert [(f.code, f.severity) for f in first.findings] == [("R5", "error"), ("R5", "error")]
    assert 'confidential term "falcon"' in first.findings[0].message
    assert '"queue.payments.internal" looks like a host name' in first.findings[1].message
    assert second.passed
    assert [(f.code, f.severity) for f in second.findings] == [("confidential", "warning")]
    assert benched == change_set.ops[2] and unreadable == change_set.ops[3]

    assert rechecked(checked, PROFILE, {}, ["falcon"]) == checked, "nothing is added twice"
    assert rechecked(change_set, PROFILE, {}, []).ops[1] == change_set.ops[1]


def test_a_bullet_drawing_on_a_sensitive_roles_facts_is_checked_for_operational_detail() -> None:
    text = "Built the recipe importer on prod-db-01."
    fact = Fact(id="f_nw", statement="Ran the importer.", role_id="northwind")
    change_set = ChangeSet(
        author="ai",
        ops=(
            ReviewedOp(
                op=AddBullet(
                    parent_id="recipe_box",
                    bullet=Bullet(id="rb_new", text=text),
                    fact_ids=("f_nw",),
                ),
                verifier_report=passed(text),
            ),
        ),
    )
    assert rechecked(change_set, PROFILE, {}, []) == change_set, "a project isn't sensitive"
    (op,) = rechecked(change_set, PROFILE, {"f_nw": fact}, []).ops
    assert op.verifier_report is not None and op.verifier_report["passed"] is False
