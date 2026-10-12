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
from app.profile_ops import apply
from app.schema.changes import (
    AddBullet,
    AddRole,
    AddSkill,
    ChangeSet,
    EditBullet,
    ResolveOpenQuestion,
    ReviewedOp,
    SetBulletStatus,
    UpdateField,
)
from app.schema.fact import Fact, Metric, SingleValue
from app.schema.profile import Bullet, Profile, Role
from app.schema.verification import VerifierReport
from app.synthesis import (
    PlannedBullet,
    PlannedEndDate,
    PlannedReactivation,
    PlannedRole,
    PlannedSkill,
    Synthesis,
    SynthesisPlan,
    new_facts,
    planning_facts_json,
    synthesis_context,
    synthesize,
)
from app.validate.claims import ClaimReply, JudgedClaim
from app.writing import WrittenBullet

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
IMPORTED = TypeAdapter(tuple[Fact, ...]).validate_json(
    (FIXTURES / "facts.json").read_text(encoding="utf-8")
)
ALERTS = Fact(
    id="e1_1",
    statement="Added an alert that pages on-call when the nightly export fails.",
    ownership="owned",
    date="2026-09",
    role_id="northwind",
    entry_id="e1",
)
VOLUME = Fact(
    id="e2_1",
    statement="The order services handle about 40,000 events a day.",
    metrics=(
        Metric(
            subject="order events a day",
            value=SingleValue(value=40000),
            unit="events",
            qualifier="approximately",
        ),
    ),
    date="2026-09",
    role_id="northwind",
    question_id="q_nw_volume",
    entry_id="e2",
)
STARTED = Fact(
    id="e3_1",
    statement="Started as a data engineer at Example Co.",
    kind="role_change",
    date="2026-10",
    proposed_role="Data Engineer, Example Co",
    entry_id="e3",
)
LOADER = Fact(
    id="e3_2",
    statement="Built a dbt model that loads sales data into the warehouse.",
    tools=("dbt",),
    ownership="owned",
    date="2026-10",
    proposed_role="Data Engineer, Example Co",
    entry_id="e3",
)
LEFT = Fact(
    id="e4_1",
    statement="Left Northwind Traders.",
    kind="role_change",
    date="2026-09",
    role_id="northwind",
    entry_id="e4",
)
BIRDS = Fact(
    id="e5_1",
    statement="Built the bird call classifier, which tells 12 species apart from short clips.",
    kind="project",
    metrics=(Metric(subject="species told apart", value=SingleValue(value=12), unit="species"),),
    tools=("Python", "PyTorch"),
    ownership="owned",
    date="2026-08",
    project_id="bird_calls",
    entry_id="e5",
)
JOURNAL = (ALERTS, STARTED, LOADER, LEFT, BIRDS)
"""New facts, apart from VOLUME, which answers an open question and so always resolves it."""
ALERT_TEXT = "Added an alert that pages on-call when the nightly export job fails."
SUPPORTED = reply(
    ClaimReply(
        claims=(JudgedClaim(claim="Added an alert", reason="As stated.", verdict="supported"),)
    )
)
ADDED_KAFKA = reply(
    ClaimReply(
        claims=(
            JudgedClaim(
                claim="with Kafka",
                reason="No fact names Kafka.",
                verdict="unsupported",
                issue="added_tool",
                term="Kafka",
            ),
        )
    )
)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def plan(**parts: Any) -> BetaMessage:
    return reply(SynthesisPlan(**parts))


def written(text: str) -> BetaMessage:
    return reply(WrittenBullet(text=text))


def run(
    *replies: BetaMessage, facts: tuple[Fact, ...] = JOURNAL, **options: Any
) -> tuple[Synthesis, FakeMessages]:
    """Synthesize with scripted replies: the plan, then the writer's and the claim check's for
    each bullet, in turn."""
    fake = FakeMessages(*replies)
    settings = Settings(llm_model_heavy="heavy-model", llm_model_standard="standard-model")
    client = LLMClient(settings, fake, MemoryCallLog())
    return synthesize(PROFILE, facts, client, sources=IMPORTED, **options), fake


def proposed(synthesis: Synthesis) -> ChangeSet:
    assert synthesis.change_set is not None, synthesis.notes
    return synthesis.change_set


def message(fake: FakeMessages, request: int) -> str:
    return fake.requests[request]["messages"][0]["content"]


def sent_facts(fake: FakeMessages, request: int) -> list[str]:
    facts = message(fake, request).split("<facts>\n")[1].split("\n</facts>")[0]
    return [fact["id"] for fact in json.loads(facts)]


def test_a_plan_becomes_a_change_set_of_verified_operations() -> None:
    bullet = PlannedBullet(under="northwind", fact_ids=("e1_1",), tags=("backend", "ops"))
    synthesis, fake = run(plan(bullets=[bullet]), written(ALERT_TEXT), SUPPORTED, base_version=3)
    change_set = proposed(synthesis)
    assert (change_set.author, change_set.status, change_set.base_version) == ("ai", "proposed", 3)
    assert change_set.summary == "From 1 new fact: 1 new bullet."
    [reviewed] = change_set.ops
    assert reviewed.op == AddBullet(
        parent_id="northwind",
        bullet=Bullet(
            id="northwind_1",
            text=ALERT_TEXT,
            strength="medium",
            verification="yes",
            tags=("backend",),
        ),
        fact_ids=("e1_1",),
    )
    assert reviewed.verifier_report is not None
    report = VerifierReport.model_validate(reviewed.verifier_report)
    assert (report.passed, report.text, report.fact_ids) == (True, ALERT_TEXT, ("e1_1",))
    assert synthesis.unused == ("e3_1", "e3_2", "e4_1", "e5_1")
    assert synthesis.notes == ()

    planner, writer = fake.requests[0], fake.requests[1]
    assert planner["model"] == "heavy-model"
    assert planner["system"][1]["text"] == synthesis_context(PROFILE)
    assert message(fake, 0) == f"The new facts:\n<facts>\n{planning_facts_json(JOURNAL)}\n</facts>"
    assert writer["model"] == "standard-model"
    assert message(fake, 1).startswith('The bullet goes under the role "northwind".')
    assert sent_facts(fake, 1) == ["e1_1"]

    applied = apply(PROFILE, [op.op for op in change_set.ops])
    assert applied.work[0].highlights[-1].sources == ("e1_1",)


def test_the_planner_sees_statuses_links_and_questions_but_no_contact_details() -> None:
    context = synthesis_context(PROFILE)
    data = json.loads(context.split("\n", 1)[1])
    northwind = data["roles"][0]
    assert [(b["id"], b["status"]) for b in northwind["bullets"]] == [
        ("nw_orders", "active"),
        ("nw_export", "active"),
        ("nw_tracker", "benched"),
    ]
    assert northwind["bullets"][0]["needs"] == ["q_nw_volume"]
    assert northwind["bullets"][2]["statusReason"] == "Tool usage, not an achievement."
    assert data["roleTypes"][0] == {"id": "backend", "name": "Backend / full stack"}
    assert data["openQuestions"][0]["status"] == "open"
    assert data["projects"][1]["status"] == "planned"
    for private in ("Casey Morgan", "casey@example.com", "555"):
        assert private not in context
    facts = json.loads(planning_facts_json((VOLUME, STARTED)))
    assert facts[0]["questionId"] == "q_nw_volume"
    assert facts[1]["proposedRole"] == "Data Engineer, Example Co"
    assert "entryId" not in facts[0]


def test_an_answer_gives_a_new_version_of_its_bullet_and_resolves_the_question() -> None:
    edit = PlannedBullet(under="northwind", edit="nw_orders", fact_ids=("e2_1",), strength="high")
    text = "Built and maintained Python services that process about 40,000 events a day."
    synthesis, fake = run(plan(bullets=[edit]), written(text), SUPPORTED, facts=(VOLUME,))
    change_set = proposed(synthesis)
    assert [op.op for op in change_set.ops] == [
        EditBullet(bullet_id="nw_orders", text=text, fact_ids=("e2_1",)),
        ResolveOpenQuestion(question_id="q_nw_volume", fact_ids=("e2_1",)),
    ]
    assert change_set.summary == (
        "From 1 new fact: 1 new version of a bullet, 1 answered open question."
    )
    assert "<current>\nBuild and maintain Python services" in message(fake, 1)


def test_a_new_version_is_written_from_its_bullets_facts_too() -> None:
    edit = PlannedBullet(under="northwind", edit="nw_export", fact_ids=("e1_1",))
    text = "Cut the nightly export job from 50 to 12 minutes and added an alert for failures."
    _, fake = run(plan(bullets=[edit]), written(text), SUPPORTED)
    assert sent_facts(fake, 1) == ["fact_nw_export", "e1_1"]
    assert sent_facts(fake, 2) == ["fact_nw_export", "e1_1"], "and checked against them"


def test_a_new_role_takes_the_owners_words_and_goes_first() -> None:
    role = PlannedRole(
        key="example",
        organisation="Example Co",
        title="Data Engineer",
        start_date="2026-10",
        fact_ids=("e3_1",),
    )
    skill = PlannedSkill(name="DBT", category="Data", fact_ids=("e3_2",))
    bullet = PlannedBullet(under="example", fact_ids=("e3_2",))
    text = "Built a dbt model that loads sales data into the warehouse."
    synthesis, fake = run(
        plan(roles=[role], skills=[skill], bullets=[bullet]), written(text), SUPPORTED
    )
    ops = [reviewed.op for reviewed in proposed(synthesis).ops]
    assert ops[0] == AddRole(
        role=Role(
            id="example_co", name="Example Co", position="Data Engineer", start_date="2026-10"
        ),
        position=0,
        fact_ids=("e3_1",),
    )
    assert isinstance(ops[1], AddSkill)
    assert (ops[1].skill.name, ops[1].skill.verify_before_shipping) == ("dbt", True)
    assert ops[1].rationale is not None and "verify-before-shipping" in ops[1].rationale
    assert isinstance(ops[2], AddBullet)
    assert (ops[2].parent_id, ops[2].bullet.id) == ("example_co", "example_co_1")
    assert message(fake, 1).startswith('The bullet goes under the role "example_co".')
    applied = apply(PROFILE, ops)
    assert [role.id for role in applied.work] == ["example_co", "northwind", "contoso"]


def test_a_new_role_needs_the_owners_words_and_a_date() -> None:
    def role(**changes: Any) -> PlannedRole:
        fields = {
            "key": "example",
            "organisation": "Example Co",
            "title": "Data Engineer",
            "start_date": "2026-10",
            "fact_ids": ("e3_1",),
        }
        return PlannedRole.model_validate({**fields, **changes})

    senior = plan(
        roles=[role(title="Senior Data Engineer")],
        bullets=[PlannedBullet(under="example", fact_ids=("e3_2",))],
    )
    synthesis, _ = run(senior)
    assert synthesis.change_set is None
    assert synthesis.notes == (
        'Left out the new role "example": its title isn\'t in your words',
        'Left out a new bullet under "example": there\'s nothing called that to put it under',
    )

    synthesis, _ = run(plan(roles=[role(start_date="2026-01", end_date="2026-03")]))
    [reviewed] = proposed(synthesis).ops
    assert isinstance(reviewed.op, AddRole)
    assert (reviewed.op.role.start_date, reviewed.op.role.end_date) == ("2026-10", None)
    assert reviewed.op.rationale == (
        "Its start date is the month of its earliest fact, 2026-10; check it."
    )

    undated = STARTED.model_copy(update={"date": None})
    synthesis, _ = run(plan(roles=[role()]), facts=(undated,))
    assert synthesis.notes == (
        'Left out the new role "example": none of its facts has a date to start it from',
    )


def test_skills_come_only_from_the_facts_tools_and_never_from_the_gaps_list() -> None:
    rusty = LOADER.model_copy(update={"id": "e3_3", "tools": ("Rust",)})
    skills = [
        PlannedSkill(name="Kafka", category="Data", fact_ids=("e3_2",)),
        PlannedSkill(name="Rust", category="Languages", fact_ids=("e3_3",)),
        PlannedSkill(name="python", category="Languages", fact_ids=("e5_1",)),
        PlannedSkill(name="dbt", category="Data", fact_ids=("e3_2",)),
        PlannedSkill(name="dbt", category="Data", fact_ids=("e3_2",)),
    ]
    synthesis, _ = run(plan(skills=skills), facts=(*JOURNAL, rusty))
    ops = [reviewed.op for reviewed in proposed(synthesis).ops]
    assert [op.skill.name for op in ops if isinstance(op, AddSkill)] == ["dbt"]
    assert len(ops) == 1
    assert synthesis.notes == (
        'Left out the skill "Kafka": the facts it cites don\'t name it',
        'Left out the skill "Rust": it\'s on your gaps list',
    )


def test_a_planned_bullet_gets_a_new_version_only_when_made_active() -> None:
    reactivations = [
        PlannedReactivation(item_id="bird_calls", fact_ids=("e5_1",)),
        PlannedReactivation(item_id="bc_model", fact_ids=("e5_1",)),
    ]
    edit = PlannedBullet(under="bird_calls", edit="bc_model", fact_ids=("e5_1",), strength="high")
    text = "Built a classifier that tells 12 species apart from short audio clips."
    synthesis, _ = run(plan(reactivations=reactivations, bullets=[edit]), written(text), SUPPORTED)
    ops = [reviewed.op for reviewed in proposed(synthesis).ops]
    assert ops == [
        UpdateField(target_id="bird_calls", field="status", value="active", fact_ids=("e5_1",)),
        SetBulletStatus(bullet_id="bc_model", status="active", fact_ids=("e5_1",)),
        EditBullet(bullet_id="bc_model", text=text, fact_ids=("e5_1",)),
    ]
    applied = apply(PROFILE, ops)
    assert (applied.projects[1].status, applied.projects[1].highlights[0].status) == (
        "active",
        "active",
    )

    benched = PlannedBullet(under="northwind", edit="nw_tracker", fact_ids=("e1_1",))
    synthesis, _ = run(plan(bullets=[benched]))
    assert synthesis.change_set is None
    assert synthesis.notes == (
        "Left out the new version of \"nw_tracker\": it's benched, and the plan doesn't make it "
        "active",
    )


def test_an_end_date_is_the_date_of_a_fact_it_cites() -> None:
    left = PlannedEndDate(role_id="northwind", end_date="2026-09", fact_ids=("e4_1",))
    synthesis, _ = run(plan(end_dates=[left]))
    assert [reviewed.op for reviewed in proposed(synthesis).ops] == [
        UpdateField(target_id="northwind", field="endDate", value="2026-09", fact_ids=("e4_1",))
    ]
    later = left.model_copy(update={"end_date": "2026-10"})
    synthesis, _ = run(plan(end_dates=[later]))
    assert synthesis.notes == (
        'Left out the end date of "northwind": it isn\'t the date of a fact it cites',
    )


def test_each_bullet_cites_new_facts_once_and_under_the_role_they_were_filed_under() -> None:
    bullets = [
        PlannedBullet(under="northwind", fact_ids=("e1_1", "fact_nw_export", "e9_9")),
        PlannedBullet(under="northwind", fact_ids=("e1_1",)),
        PlannedBullet(under="contoso", fact_ids=("e4_1",)),
    ]
    synthesis, _ = run(plan(bullets=bullets), written(ALERT_TEXT), SUPPORTED, facts=JOURNAL)
    [reviewed] = proposed(synthesis).ops
    assert reviewed.op.fact_ids == ("e1_1",)
    assert synthesis.notes == (
        'A new bullet under "northwind" cited facts that aren\'t new: fact_nw_export, e9_9',
        'A new bullet under "northwind" cited facts another bullet uses: e1_1',
        'Left out a new bullet under "northwind": all its facts are in other bullets',
        'A new bullet under "contoso" cited facts you filed under another role: e4_1',
        'Left out a new bullet under "contoso": its facts belong to another role',
    )


def test_a_bullet_that_fails_the_checks_is_proposed_flagged() -> None:
    bullet = PlannedBullet(under="northwind", fact_ids=("e1_1",))
    kafka = "Added an alert with Kafka that pages on-call when the nightly export fails."
    synthesis, _ = run(
        plan(bullets=[bullet]), written(kafka), ADDED_KAFKA, written(kafka), ADDED_KAFKA
    )
    [reviewed] = proposed(synthesis).ops
    assert isinstance(reviewed.op, AddBullet)
    assert reviewed.op.bullet.verification == "no"
    assert reviewed.verifier_report is not None
    assert VerifierReport.model_validate(reviewed.verifier_report).passed is False


def test_nothing_to_propose() -> None:
    synthesis, fake = run(plan())
    assert synthesis == Synthesis(
        change_set=None, unused=tuple(fact.id for fact in JOURNAL), notes=()
    )
    assert len(fake.requests) == 1
    synthesis, fake = run(facts=())
    assert (synthesis, fake.requests) == (Synthesis(change_set=None), [])


def test_new_facts_are_journal_facts_nothing_cites_yet() -> None:
    imported = [fact.model_copy(update={"origin": "import"}) for fact in IMPORTED]
    in_a_bullet = ALERTS.model_copy(update={"id": "fact_nw_export"})
    reviewed = ChangeSet(
        author="ai",
        status="rejected",
        ops=(ReviewedOp(op=ResolveOpenQuestion(question_id="q_nw_volume", fact_ids=("e2_1",))),),
    )
    facts = [*imported, in_a_bullet, VOLUME, *JOURNAL]
    assert [fact.id for fact in new_facts(PROFILE, facts, [reviewed])] == [
        "e1_1",
        "e3_1",
        "e3_2",
        "e4_1",
        "e5_1",
    ]
    answered = PROFILE.model_copy(
        update={
            "open_questions": (
                PROFILE.open_questions[0].model_copy(update={"answer_fact_ids": ("e2_1",)}),
            )
        }
    )
    assert "e2_1" not in [fact.id for fact in new_facts(answered, [VOLUME])]
