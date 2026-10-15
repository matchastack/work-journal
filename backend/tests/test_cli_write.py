import json
import re
from pathlib import Path

import pytest
from anthropic.types.beta import BetaMessage
from typer.testing import CliRunner, Result

from app import cli as cli_module
from app.cli import cli
from app.config import Settings
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, reply
from app.llm.usage import MemoryCallLog
from app.schema.profile import Profile, Swap
from app.validate.claims import ClaimReply, JudgedClaim
from app.writing import WrittenBullet

FIXTURES = Path(__file__).parent / "fixtures"
POSTING = FIXTURES / "postings" / "backend.json"
GOOD = "Cut the nightly export job from 50 to 12 minutes by batching PostgreSQL writes."
SUPPORTED = reply(
    ClaimReply(
        claims=(
            JudgedClaim(
                claim="Cut the nightly export job",
                reason="The fact says so.",
                verdict="supported",
                fact_ids=("fact_nw_export",),
            ),
        )
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
ANSI_STYLE = re.compile(r"\x1b\[[0-9;]*m")
"""In GitHub Actions, Typer colors the options named in usage errors."""
runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def use_fake(monkeypatch: pytest.MonkeyPatch, *replies: BetaMessage) -> FakeMessages:
    fake = FakeMessages(*replies)
    llm = LLMClient(Settings(llm_model_standard="standard-model"), fake, MemoryCallLog())
    monkeypatch.setattr(cli_module, "_llm_client", lambda: llm)
    return fake


def written(text: str) -> BetaMessage:
    return reply(WrittenBullet(text=text))


def write(*options: str) -> Result:
    files = ["--profile", str(FIXTURES / "profile.json")]
    files += ["--facts-file", str(FIXTURES / "facts.json")]
    return runner.invoke(cli, ["write", *files, *options])


def own_profile(tmp_path: Path) -> Path:
    """A copy of the fictional profile that a test may change."""
    path = tmp_path / "profile.json"
    path.write_text((FIXTURES / "profile.json").read_text(encoding="utf-8"), encoding="utf-8")
    return path


def write_to(profile: Path, *options: str, answer: str) -> Result:
    files = ["--profile", str(profile), "--facts-file", str(FIXTURES / "facts.json")]
    return runner.invoke(cli, ["write", *files, *options], input=answer)


def swaps_of(profile: Path, bullet_id: str) -> tuple[Swap, ...]:
    loaded = Profile.model_validate_json(profile.read_text(encoding="utf-8"))
    bullets = [bullet for role in loaded.work for bullet in role.highlights]
    return next(bullet for bullet in bullets if bullet.id == bullet_id).swaps


def sent_facts(fake: FakeMessages) -> list[str]:
    message = fake.requests[0]["messages"][0]["content"]
    facts = json.loads(message.split("<facts>\n")[1].split("\n</facts>")[0])
    return [fact["id"] for fact in facts]


def test_write_prints_the_bullet_and_what_the_checks_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = use_fake(monkeypatch, written(GOOD), SUPPORTED)
    result = write("--facts", "fact_nw_export")
    assert result.exit_code == 0, result.output
    assert result.stdout == f"{GOOD}\n"
    assert result.stderr == "Passed the checks.\n"
    assert fake.requests[0]["messages"][0]["content"].startswith(
        'The bullet goes under the role "northwind".'
    ), "the item the facts belong to"


def test_write_flags_a_bullet_that_fails_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    kafka = "Cut the nightly export job from 50 to 12 minutes with Kafka."
    use_fake(monkeypatch, written(kafka), ADDED_KAFKA, written(kafka), ADDED_KAFKA)
    result = write("--facts", "fact_nw_export")
    assert result.exit_code == 1
    assert result.stdout == f"{kafka}\n"
    assert result.stderr.splitlines() == [
        'error    claims   "Kafka" names a tool the cited facts don\'t',
        "Flagged: it didn't pass the checks, so don't use it as it is.",
    ]


def test_write_says_when_the_second_try_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    first = "Cut the nightly export job from 50 to 10 minutes."
    use_fake(monkeypatch, written(first), SUPPORTED, written(GOOD), SUPPORTED)
    result = write("--facts", "fact_nw_export")
    assert result.exit_code == 0, result.output
    assert result.stderr == "Passed the checks when written the second time.\n"


def test_an_edit_adds_the_facts_the_bullet_was_written_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = use_fake(monkeypatch, written(GOOD), SUPPORTED)
    result = write("--facts", "fact_nw_queue", "--edit", "nw_export")
    assert result.exit_code == 0, result.output
    assert sent_facts(fake) == ["fact_nw_export", "fact_nw_queue"]
    assert "<current>\nCut the nightly export job" in fake.requests[0]["messages"][0]["content"]


def test_facts_from_extract_can_be_written_under_a_named_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extracted = tmp_path / "facts.json"
    fact = {"id": "fact_1", "statement": "Gave a talk on the export job.", "kind": "talk"}
    extracted.write_text(json.dumps([fact]), encoding="utf-8")
    talk = "Gave a talk on the export job."
    fake = use_fake(monkeypatch, written(talk), SUPPORTED)
    result = runner.invoke(
        cli,
        [
            "write",
            "--profile",
            str(FIXTURES / "profile.json"),
            "--facts-file",
            str(extracted),
            "--facts",
            "fact_1",
            "--under",
            "northwind",
        ],
    )
    assert result.exit_code == 0, result.output
    assert sent_facts(fake) == ["fact_1"]


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (["--facts", "fact_nw_billing"], "there's no fact 'fact_nw_billing'"),
        (["--facts", " , "], "name at least one fact"),
        (["--facts", "fact_nw_export,fact_rb_app"], "don't all belong to one role or project"),
        (["--facts", "fact_nw_export", "--under", "fabrikam"], "no role, education entry"),
        (["--facts", "fact_nw_export", "--edit", "nw_billing"], "no bullet 'nw_billing'"),
        (["--facts", "fact_nw_export", "--db"], "either --facts-file or --db"),
        (["--edit", "nw_tracker"], "name at least one fact"),
        (["--facts", "fact_nw_export", "--style", "posting"], "--style posting needs a posting"),
        (["--facts", "fact_nw_export", "--posting", str(POSTING)], "only it takes one"),
        (
            [
                "--facts",
                "fact_nw_export",
                "--style",
                "posting",
                "--posting",
                str(FIXTURES / "facts.json"),
            ],
            "isn't a parsed posting",
        ),
        (["--facts", "fact_nw_export", "--swap", "1"], "goes with --edit"),
        (["--facts", "fact_nw_export", "--save-swap"], "goes with --edit"),
        (
            ["--edit", "nw_orders", "--swap", "1", "--style", "linkedin"],
            "use --style resume or posting",
        ),
        (["--edit", "nw_orders", "--swap", "1", "--save-swap"], "without --save-swap"),
        (
            ["--facts", "fact_nw_queue", "--edit", "nw_orders", "--swap", "2"],
            "nw_orders has 1 swap",
        ),
    ],
)
def test_write_explains_what_it_cant_do(
    monkeypatch: pytest.MonkeyPatch, options: list[str], message: str
) -> None:
    use_fake(monkeypatch)
    result = write(*options)
    assert result.exit_code == 2
    error = ANSI_STYLE.sub("", result.stderr).replace("\u2502", " ")
    assert message in " ".join(error.split()), "the error box's borders and line breaks aside"


def test_write_needs_the_model_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = LLMClient(Settings(), FakeMessages(), MemoryCallLog())
    monkeypatch.setattr(cli_module, "_llm_client", lambda: llm)
    result = write("--facts", "fact_nw_export")
    assert result.exit_code == 1
    assert "set LLM_MODEL_STANDARD" in result.stderr


def test_write_in_the_linkedin_style(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "I cut the nightly export job from 50 to 12 minutes by batching PostgreSQL writes."
    fake = use_fake(monkeypatch, written(text), SUPPORTED)
    result = write("--style", "linkedin", "--edit", "nw_export")
    assert result.exit_code == 0, result.output
    assert result.stdout == f"{text}\n"
    assert fake.requests[0]["system"][0]["text"].startswith(
        "You write one short passage for someone's LinkedIn profile"
    )
    assert sent_facts(fake) == ["fact_nw_export"], "the bullet's own facts are enough"


def test_write_in_a_postings_words(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use_fake(monkeypatch, written(GOOD), SUPPORTED)
    result = write("--facts", "fact_nw_export", "--style", "posting", "--posting", str(POSTING))
    assert result.exit_code == 0, result.output
    message = fake.requests[0]["messages"][0]["content"]
    assert '"title": "Senior Backend Engineer, Payments"' in message
    assert "<posting_terms>\n- APIs" in fake.requests[1]["messages"][0]["content"]


def test_write_works_a_swap_into_its_bullet(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Built event-driven Python services that process order events from a message queue."
    fake = use_fake(monkeypatch, written(text), SUPPORTED)
    result = write("--facts", "fact_nw_queue", "--edit", "nw_orders", "--swap", "1")
    assert result.exit_code == 0, result.output
    assert result.stdout == f"{text}\n"
    message = fake.requests[0]["messages"][0]["content"]
    assert "<swap>\nevent-driven Python services\n</swap>" in message
    assert "<replaces>\nPython services\n</replaces>" in message


def test_an_approved_phrasing_is_saved_as_a_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = own_profile(tmp_path)
    text = "I cut the nightly export job from 50 to 12 minutes by batching PostgreSQL writes."
    use_fake(monkeypatch, written(text), SUPPORTED)
    options = ("--style", "linkedin", "--edit", "nw_export", "--save-swap")
    result = write_to(profile, *options, answer="y\n")
    assert result.exit_code == 0, result.output
    assert "Save this phrasing as a swap on nw_export? [Y/n]" in result.stdout
    assert f"Saved the swap on nw_export to {profile}." in result.stdout
    assert swaps_of(profile, "nw_export") == (
        Swap(text=text, contexts=("LinkedIn",), origin="approved"),
    )


def test_a_phrasing_is_saved_only_when_you_say_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = own_profile(tmp_path)
    before = profile.read_text(encoding="utf-8")
    use_fake(monkeypatch, written(GOOD), SUPPORTED)
    options = ("--style", "posting", "--posting", str(POSTING), "--edit", "nw_export")
    result = write_to(profile, *options, "--save-swap", answer="n\n")
    assert result.exit_code == 0, result.output
    assert "Nothing was saved." in result.stdout
    assert profile.read_text(encoding="utf-8") == before


def test_a_flagged_phrasing_is_not_saved_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = own_profile(tmp_path)
    kafka = "I cut the nightly export job from 50 to 12 minutes with Kafka."
    use_fake(monkeypatch, written(kafka), ADDED_KAFKA, written(kafka), ADDED_KAFKA)
    options = ("--style", "linkedin", "--edit", "nw_export", "--save-swap")
    result = write_to(profile, *options, answer="\n")
    assert result.exit_code == 1
    assert "It was flagged. Save it as a swap on nw_export anyway? [y/N]" in result.stdout
    assert swaps_of(profile, "nw_export") == ()
