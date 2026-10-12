"""`wj propose` and `wj apply` on local files: propose changes from new facts, then review them."""

import json
from pathlib import Path

import pytest
from anthropic.types.beta import BetaMessage
from pydantic import TypeAdapter
from typer.testing import CliRunner, Result

from app import cli as cli_module
from app.cli import cli
from app.config import Settings
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, reply
from app.llm.usage import MemoryCallLog
from app.schema.changes import ChangeSet
from app.schema.fact import Fact
from app.schema.profile import Profile
from app.synthesis import PlannedBullet, PlannedRole, SynthesisPlan
from app.validate.claims import ClaimReply, JudgedClaim
from app.writing import WrittenBullet

FIXTURES = Path(__file__).parent / "fixtures"
IMPORTED = [
    fact.model_copy(update={"origin": "import"})
    for fact in TypeAdapter(list[Fact]).validate_json(
        (FIXTURES / "facts.json").read_text(encoding="utf-8")
    )
]
ALERTS = Fact(
    id="e1_1",
    statement="Added an alert that pages on-call when the nightly export fails.",
    ownership="owned",
    date="2026-09",
    role_id="northwind",
)
STARTED = Fact(
    id="e3_1",
    statement="Started as a data engineer at Example Co.",
    kind="role_change",
    date="2026-10",
    proposed_role="Data Engineer, Example Co",
)
LOADER = Fact(
    id="e3_2",
    statement="Built a dbt model that loads sales data into the warehouse.",
    tools=("dbt",),
    date="2026-10",
    proposed_role="Data Engineer, Example Co",
)
ALERT_TEXT = "Added an alert that pages on-call when the nightly export job fails."
LOADER_TEXT = "Built a dbt model that loads sales data into the warehouse."
PLAN = SynthesisPlan(
    roles=(
        PlannedRole(
            key="example",
            organisation="Example Co",
            title="Data Engineer",
            start_date="2026-10",
            fact_ids=("e3_1",),
        ),
    ),
    bullets=(
        PlannedBullet(under="northwind", fact_ids=("e1_1",)),
        PlannedBullet(under="example", fact_ids=("e3_2",)),
    ),
)
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
runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture
def files(tmp_path: Path) -> Path:
    """A folder with the fictional profile, its imported facts and three journal facts."""
    (tmp_path / "profile.json").write_text(
        (FIXTURES / "profile.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    facts = [fact.model_dump(mode="json") for fact in (*IMPORTED, ALERTS, STARTED, LOADER)]
    (tmp_path / "facts.json").write_text(json.dumps(facts), encoding="utf-8")
    return tmp_path


def use_fake(monkeypatch: pytest.MonkeyPatch, *replies: BetaMessage) -> FakeMessages:
    fake = FakeMessages(*replies)
    settings = Settings(llm_model_heavy="heavy-model", llm_model_standard="standard-model")
    llm = LLMClient(settings, fake, MemoryCallLog())
    monkeypatch.setattr(cli_module, "_llm_client", lambda: llm)
    return fake


def invoke(command: str, folder: Path, *options: str, answers: str | None = None) -> Result:
    paths = ["--profile", str(folder / "profile.json"), "--facts-file", str(folder / "facts.json")]
    paths += ["--change-set", str(folder / "change-set.json")]
    return runner.invoke(cli, [command, *paths, *options], input=answers)


def propose(folder: Path, monkeypatch: pytest.MonkeyPatch, *replies: BetaMessage) -> Result:
    if not replies:
        replies = (reply(PLAN), reply(WrittenBullet(text=ALERT_TEXT)), SUPPORTED)
        replies += (reply(WrittenBullet(text=LOADER_TEXT)), SUPPORTED)
    use_fake(monkeypatch, *replies)
    result = invoke("propose", folder)
    assert result.exit_code == 0, result.output
    return result


def saved(folder: Path) -> tuple[Profile, ChangeSet]:
    profile = Profile.model_validate_json((folder / "profile.json").read_text(encoding="utf-8"))
    change_set = ChangeSet.model_validate_json(
        (folder / "change-set.json").read_text(encoding="utf-8")
    )
    return profile, change_set


def test_propose_writes_the_change_set_and_shows_each_change(
    files: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = propose(files, monkeypatch)
    assert result.stdout.splitlines() == [
        "Planning changes from 3 new facts.",
        "From 3 new facts: 1 new role, 2 new bullets.",
        "",
        "1. New role example_co: Data Engineer at Example Co, 2026-10 to now",
        "   From e3_1: Started as a data engineer at Example Co.",
        "",
        "2. New bullet under northwind (Engineer at Northwind Traders):",
        f"     {ALERT_TEXT}",
        "   From e1_1: Added an alert that pages on-call when the nightly export fails.",
        "   Passed the checks.",
        "",
        "3. New bullet under example_co:",
        f"     {LOADER_TEXT}",
        "   From e3_2: Built a dbt model that loads sales data into the warehouse.",
        "   Passed the checks.",
        f"Wrote {files / 'change-set.json'}. Review it with `wj apply`.",
    ]
    profile, change_set = saved(files)
    assert change_set.status == "proposed"
    assert len(change_set.ops) == 3
    assert profile == Profile.model_validate_json(
        (FIXTURES / "profile.json").read_text(encoding="utf-8")
    ), "nothing changes before the review"

    use_fake(monkeypatch)
    again = invoke("propose", files)
    assert again.exit_code == 1
    assert "still waits for review: run `wj apply` first" in again.stderr


def test_apply_with_yes_accepts_what_passed_and_updates_the_profile(
    files: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    propose(files, monkeypatch)
    result = invoke("apply", files, "--yes")
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[-2:] == [
        "Accepted 3 of 3 changes.",
        f"Wrote {files / 'profile.json'}. Render the master resume with `wj render`.",
    ]
    profile, change_set = saved(files)
    assert change_set.status == "applied"
    assert {reviewed.status for reviewed in change_set.ops} == {"accepted"}
    assert [role.id for role in profile.work] == ["example_co", "northwind", "contoso"]
    assert [bullet.text for bullet in profile.work[0].highlights] == [LOADER_TEXT]
    assert profile.work[1].highlights[-1].sources == ("e1_1",)

    use_fake(monkeypatch)
    again = invoke("apply", files)
    assert again.exit_code == 1
    assert "was already applied" in again.stderr
    nothing_new = invoke("propose", files)
    assert nothing_new.exit_code == 0, nothing_new.output
    assert nothing_new.stdout == "There are no new facts to propose changes from.\n"


def test_rejecting_a_new_role_rejects_the_bullets_under_it(
    files: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    propose(files, monkeypatch)
    answers = "n\nNot yet.\ny\ny\n"
    result = invoke("apply", files, answers=answers)
    assert result.exit_code == 0, result.output
    assert "   Rejected: it needs change 1, which you rejected." in result.stdout
    assert "Accepted 1 of 3 changes." in result.stdout
    profile, change_set = saved(files)
    assert [(op.status, op.reason) for op in change_set.ops] == [
        ("rejected", "Not yet."),
        ("accepted", None),
        ("rejected", "It needs change 1, which was rejected."),
    ]
    assert [role.id for role in profile.work] == ["northwind", "contoso"]
    assert profile.work[0].highlights[-1].text == ALERT_TEXT


def test_a_flagged_change_is_rejected_unless_you_accept_it(
    files: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kafka = "Added an alert with Kafka that pages on-call when the nightly export fails."
    plan = SynthesisPlan(bullets=(PlannedBullet(under="northwind", fact_ids=("e1_1",)),))
    flagged = (reply(WrittenBullet(text=kafka)), ADDED_KAFKA) * 2
    result = propose(files, monkeypatch, reply(plan), *flagged)
    assert "   Flagged: it didn't pass the checks, so don't use it as it is." in result.stdout
    assert "Not used this time: e3_1, e3_2. They stay in your journal." in result.stdout

    declined = invoke("apply", files, answers="\n\n")
    assert declined.exit_code == 0, declined.output
    assert "Accept this change? [y/N]" in declined.stdout
    assert "You rejected every change, so the profile stays as it was." in declined.stdout
    _, change_set = saved(files)
    assert (change_set.status, change_set.ops[0].status) == ("rejected", "rejected")


def test_apply_with_yes_never_accepts_a_flagged_change(
    files: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kafka = "Added an alert with Kafka that pages on-call when the nightly export fails."
    plan = SynthesisPlan(bullets=(PlannedBullet(under="northwind", fact_ids=("e1_1",)),))
    propose(files, monkeypatch, reply(plan), *(reply(WrittenBullet(text=kafka)), ADDED_KAFKA) * 2)
    result = invoke("apply", files, "--yes")
    assert result.exit_code == 0, result.output
    assert "   Rejected: the checks flagged it." in result.stdout
    _, change_set = saved(files)
    assert change_set.ops[0].reason == "The checks flagged it."


def test_nothing_is_written_when_you_dont_confirm(
    files: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    propose(files, monkeypatch)
    result = invoke("apply", files, answers="y\ny\ny\nn\n")
    assert result.exit_code == 0, result.output
    assert result.stdout.endswith("Nothing was written.\n")
    profile, change_set = saved(files)
    assert change_set.status == "proposed"
    assert [role.id for role in profile.work] == ["northwind", "contoso"]


def test_propose_without_new_facts_calls_no_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = use_fake(monkeypatch)
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps([fact.model_dump(mode="json") for fact in IMPORTED]), "utf-8")
    result = runner.invoke(
        cli,
        ["propose", "--profile", str(FIXTURES / "profile.json"), "--facts-file", str(facts)],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout == "There are no new facts to propose changes from.\n"
    assert fake.requests == []


def test_two_facts_with_one_id_are_refused(files: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake(monkeypatch)
    facts = str(files / "facts.json")
    result = invoke("propose", files, "--facts-file", facts)
    assert result.exit_code == 2
    assert "two facts have the ID" in " ".join(result.stderr.split())
