import json
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import httpx2
import pytest
from anthropic import APIConnectionError
from anthropic.types.beta import BetaMessage
from pydantic import TypeAdapter

from app.config import Settings
from app.llm.client import LLMClient, LLMUnavailable
from app.llm.fake import Answer, FakeMessages, output_name, prompt_text, reply
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
from app.tick import JOB_GRACE_S, TICK_BUDGET_S
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
    return prompt_text(fake.requests[request])


def facts_in(request: dict[str, Any]) -> list[str]:
    """The IDs of the facts a writer's or claim check's request sends."""
    facts = prompt_text(request).split("<facts>\n")[1].split("\n</facts>")[0]
    return [fact["id"] for fact in json.loads(facts)]


def sent_facts(fake: FakeMessages, request: int) -> list[str]:
    return facts_in(fake.requests[request])


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


# --- Bullets written several at a time (T-064) ---------------------------------------------------

Delay = Callable[[str, list[str]], float]
"""How long Claude takes, in seconds, to reply with an output about the facts a request sends."""
DIGITS_AS_LETTERS = str.maketrans("0123456789", "abcdefghij")


def alerts(count: int) -> tuple[Fact, ...]:
    """`count` new facts filed under northwind: `e1_1`, `e2_1` and so on."""
    return tuple(
        ALERTS.model_copy(update={"id": f"e{number}_1", "entry_id": f"e{number}"})
        for number in range(1, count + 1)
    )


def text_for(fact_ids: Sequence[str]) -> str:
    """A bullet that names the facts it's written from. It has no digits: the number check would
    flag them, and the bullet would be written again."""
    names = " and ".join(fact_id.translate(DIGITS_AS_LETTERS) for fact_id in fact_ids)
    return f"Added alerts for the {names} exports."


def by_request(planned: SynthesisPlan, delay: Delay = lambda output, facts: 0) -> Answer:
    """Answers each request by what it asks, as calls from several threads need: the plan, a
    bullet named after its facts, and a claim check that finds it supported."""

    def answer(request: dict[str, Any]) -> BetaMessage:
        output = output_name(request)
        if output == "SynthesisPlan":
            return reply(planned)
        facts = facts_in(request)
        time.sleep(delay(output, facts))
        if output == "WrittenBullet":
            return written(text_for(facts))
        assert output == "ClaimReply", output
        return SUPPORTED

    return answer


def answered(
    answer: Answer, facts: tuple[Fact, ...], concurrency: int
) -> tuple[Synthesis, FakeMessages]:
    """Synthesize, with each request answered by `answer`."""
    fake = FakeMessages(answer=answer)
    settings = Settings(llm_model_heavy="heavy-model", llm_model_standard="standard-model")
    client = LLMClient(settings, fake, MemoryCallLog())
    synthesis = synthesize(PROFILE, facts, client, sources=IMPORTED, concurrency=concurrency)
    return synthesis, fake


def one_each(facts: Sequence[Fact]) -> SynthesisPlan:
    """A plan of one new bullet under northwind for each fact."""
    return SynthesisPlan(
        bullets=tuple(PlannedBullet(under="northwind", fact_ids=(fact.id,)) for fact in facts)
    )


def test_bullets_are_written_up_to_the_concurrency_at_once() -> None:
    """After the first, which goes alone, so that the profile context it caches serves the rest."""
    facts = alerts(7)
    calls = by_request(one_each(facts))
    # Holds each write after the first until three are in flight; one by one, it times out.
    together = threading.Barrier(3, timeout=5)
    lock = threading.Lock()
    in_flight = peak = 0

    def answer(request: dict[str, Any]) -> BetaMessage | Exception:
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        try:
            if output_name(request) == "WrittenBullet" and facts_in(request) != ["e1_1"]:
                together.wait()
            return calls(request)
        finally:
            with lock:
                in_flight -= 1

    synthesis, fake = answered(answer, facts, concurrency=3)
    assert peak == 3
    assert [output_name(request) for request in fake.requests[:3]] == [
        "SynthesisPlan",
        "WrittenBullet",
        "ClaimReply",
    ]
    assert facts_in(fake.requests[1]) == facts_in(fake.requests[2]) == ["e1_1"]
    assert len(fake.requests) == 1 + 2 * 7
    ops = [reviewed.op for reviewed in proposed(synthesis).ops]
    assert [(op.bullet.id, op.fact_ids) for op in ops if isinstance(op, AddBullet)] == [
        (f"northwind_{number}", (f"e{number}_1",)) for number in range(1, 8)
    ]


def test_the_change_set_is_the_same_whatever_finishes_first() -> None:
    """Places, IDs, facts and notes are settled from the plan, and results kept in its order."""
    recipes = Fact(
        id="e8_1",
        statement="Added offline mode to the recipe box app.",
        date="2026-09",
        project_id="recipe_box",
        entry_id="e8",
    )
    facts = (*alerts(7), recipes)
    planned = SynthesisPlan(
        bullets=(
            PlannedBullet(under="northwind", fact_ids=("e1_1",)),
            PlannedBullet(under="northwind", edit="nw_export", fact_ids=("e2_1",)),
            PlannedBullet(under="recipe_box", fact_ids=("e8_1",)),
            PlannedBullet(under="northwind", fact_ids=("e1_1", "e3_1")),
            PlannedBullet(under="northwind", fact_ids=("e2_1",)),
            PlannedBullet(under="northwind", fact_ids=("e4_1", "e5_1")),
            PlannedBullet(under="northwind", edit="nw_orders", fact_ids=("e6_1",)),
        )
    )
    finished: list[str] = []

    def later_first(output: str, facts: list[str]) -> float:
        """The lower the number of a bullet's last fact, the slower its replies."""
        return 0.01 * (10 - int(facts[-1][1:].split("_")[0]))

    slow = by_request(planned, later_first)

    def answer(request: dict[str, Any]) -> BetaMessage | Exception:
        result = slow(request)
        if output_name(request) == "ClaimReply":
            finished.append(facts_in(request)[-1])
        return result

    together, _ = answered(answer, facts, concurrency=4)
    one_by_one, _ = answered(by_request(planned), facts, concurrency=1)
    assert finished[0] == "e1_1"
    assert finished != ["e1_1", "e2_1", "e8_1", "e3_1", "e5_1", "e6_1"], "not in the plan's order"
    assert together == one_by_one
    ops = [reviewed.op for reviewed in proposed(together).ops]
    assert [
        (op.bullet.id, op.fact_ids, op.bullet.text)
        if isinstance(op, AddBullet)
        else (op.bullet_id, op.fact_ids, op.text)
        for op in ops
        if isinstance(op, AddBullet | EditBullet)
    ] == [
        ("northwind_1", ("e1_1",), text_for(["e1_1"])),
        ("nw_export", ("e2_1",), text_for(["fact_nw_export", "e2_1"])),
        ("recipe_box_1", ("e8_1",), text_for(["e8_1"])),
        ("northwind_2", ("e3_1",), text_for(["e3_1"])),
        ("northwind_3", ("e4_1", "e5_1"), text_for(["e4_1", "e5_1"])),
        ("nw_orders", ("e6_1",), text_for(["e6_1"])),
    ]
    assert len(ops) == 6
    assert together.notes == (
        'A new bullet under "northwind" cited facts another bullet uses: e1_1',
        'A new bullet under "northwind" cited facts another bullet uses: e2_1',
        'Left out a new bullet under "northwind": all its facts are in other bullets',
    )
    assert together.unused == ("e7_1",)


def test_a_bullet_that_fails_stops_the_rest() -> None:
    """The refresh fails as before: the error is raised, and no more bullets start."""
    facts = alerts(9)
    calls = by_request(one_each(facts), delay=lambda output, facts: 0.05)

    def answer(request: dict[str, Any]) -> BetaMessage | Exception:
        if output_name(request) == "WrittenBullet" and facts_in(request) == ["e2_1"]:
            return APIConnectionError(
                request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            )
        return calls(request)

    fake = FakeMessages(answer=answer)
    settings = Settings(llm_model_heavy="heavy-model", llm_model_standard="standard-model")
    client = LLMClient(settings, fake, MemoryCallLog())
    with pytest.raises(LLMUnavailable):
        synthesize(PROFILE, facts, client, sources=IMPORTED, concurrency=2)
    writes = [
        facts_in(request) for request in fake.requests if output_name(request) == "WrittenBullet"
    ]
    assert writes[0] == ["e1_1"]
    assert ["e2_1"] in writes
    assert len(writes) < 9


def test_bullets_are_written_at_least_one_at_a_time() -> None:
    with pytest.raises(ValueError, match="at least one at a time"):
        answered(by_request(one_each(JOURNAL)), JOURNAL, concurrency=0)


SECOND = 0.01
"""One second of Claude's time, in the test's time."""
PLAN_S = 120
"""The heavy-tier plan of 100 facts: about 20k tokens in, and 4-6k out at about 50 a second."""
WRITE_S = 5
"""Writing one bullet."""
CHECK_S = 7
"""The claim check of one bullet."""
CONCURRENCY = 10


def test_the_bullets_of_a_hundred_facts_fit_in_the_time_the_plan_leaves() -> None:
    """A refresh of months of facts finishes in the time a tick gives its job: the planner
    combines related facts, so 100 facts make about 50 bullets. One by one, they'd take 600 s."""
    facts = alerts(100)
    planned = SynthesisPlan(
        bullets=tuple(
            PlannedBullet(under="northwind", fact_ids=(facts[pair].id, facts[pair + 1].id))
            for pair in range(0, 100, 2)
        )
    )
    latency = {"WrittenBullet": WRITE_S, "ClaimReply": CHECK_S}
    answer = by_request(planned, lambda output, facts: latency[output] * SECOND)
    started = time.perf_counter()
    synthesis, fake = answered(answer, facts, concurrency=CONCURRENCY)
    elapsed = time.perf_counter() - started
    assert elapsed < (TICK_BUDGET_S + JOB_GRACE_S - PLAN_S) * SECOND
    ops = [reviewed.op for reviewed in proposed(synthesis).ops]
    assert [op.fact_ids for op in ops] == [bullet.fact_ids for bullet in planned.bullets]
    assert len(fake.requests) == 1 + 2 * 50, "every bullet passed at its first try"
