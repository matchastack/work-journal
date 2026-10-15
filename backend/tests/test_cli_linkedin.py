from pathlib import Path

import pytest
from anthropic.types.beta import BetaMessage
from typer.testing import CliRunner, Result

from app import cli as cli_module
from app.cli import cli
from app.config import Settings
from app.linkedin import LinkedInDone, LinkedInPack, WrittenSection
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, reply
from app.llm.usage import MemoryCallLog
from app.validate.claims import ClaimReply, JudgedClaim

FIXTURES = Path(__file__).parent / "fixtures"
TEXTS = [
    "Software Engineer | Python services and data tooling",
    "I build Python services at Northwind Traders.",
    "I build Python services that process order events.",
    "I built a Python tool that labelled sensor logs.",
]
SUPPORTED = reply(
    ClaimReply(
        claims=(JudgedClaim(claim="Python", reason="The facts say so.", verdict="supported"),)
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
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setattr(cli_module, "LINKEDIN_PACK_FILE", tmp_path / "linkedin" / "pack.json")
    monkeypatch.setattr(cli_module, "LINKEDIN_DONE_FILE", tmp_path / "linkedin" / "done.json")


def use_fake(monkeypatch: pytest.MonkeyPatch, *replies: BetaMessage) -> FakeMessages:
    fake = FakeMessages(*replies)
    llm = LLMClient(Settings(llm_model_standard="standard-model"), fake, MemoryCallLog())
    monkeypatch.setattr(cli_module, "_llm_client", lambda: llm)
    return fake


def passing(*texts: str) -> list[BetaMessage]:
    return [m for text in texts for m in (reply(WrittenSection(text=text)), SUPPORTED)]


def linkedin(*options: str) -> Result:
    return runner.invoke(cli, ["linkedin", "--profile", str(FIXTURES / "profile.json"), *options])


def test_linkedin_prints_every_section_of_the_first_pack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_fake(monkeypatch, *passing(*TEXTS))
    result = linkedin()
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        f"== Headline ({len(TEXTS[0])}/220 characters) ==",
        TEXTS[0],
        "",
        f"== About ({len(TEXTS[1])}/2,600 characters) ==",
        TEXTS[1],
        "",
        f"== Position: Software Engineer at Northwind Traders ({len(TEXTS[2])}/2,000 "
        "characters) ==",
        TEXTS[2],
        "",
        f"== Position: Research Intern at Contoso Research ({len(TEXTS[3])}/2,000 characters) ==",
        TEXTS[3],
        "",
        "Once you've pasted them into LinkedIn, run `wj linkedin done`.",
    ]
    assert result.stderr == "Passed the checks.\n" * 4
    saved = LinkedInPack.model_validate_json((tmp_path / "linkedin" / "pack.json").read_text())
    assert [section.text for section in saved.sections] == TEXTS


def test_after_done_only_changed_sections_are_printed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_fake(monkeypatch, *passing(*TEXTS))
    assert linkedin().exit_code == 0
    done = runner.invoke(cli, ["linkedin", "done"])
    assert done.exit_code == 0, done.output
    assert done.stdout == "Marked 4 sections done.\n"
    snapshot = LinkedInDone.model_validate_json((tmp_path / "linkedin" / "done.json").read_text())
    assert [section.text for section in snapshot.sections] == TEXTS

    fake = use_fake(monkeypatch)
    again = linkedin()
    assert again.exit_code == 0, again.output
    assert fake.requests == [], "nothing changed, so nothing is written"
    assert again.stdout == "Nothing changed since you last marked the pack done.\n"


def test_all_prints_every_section_again(monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake(monkeypatch, *passing(*TEXTS))
    assert linkedin().exit_code == 0
    assert runner.invoke(cli, ["linkedin", "done"]).exit_code == 0
    use_fake(monkeypatch, *passing(*TEXTS))
    result = linkedin("--all")
    assert result.exit_code == 0, result.output
    assert result.stdout.count("== ") == 4
    assert result.stdout.endswith("Nothing changed since you last marked the pack done.\n")


def test_a_flagged_section_exits_with_status_1_and_is_not_marked_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kafka = "I build Python services with Kafka."
    replies = [*passing(*TEXTS[:2])]
    replies += [reply(WrittenSection(text=kafka)), ADDED_KAFKA] * 2
    replies += passing(TEXTS[3])
    use_fake(monkeypatch, *replies)
    result = linkedin()
    assert result.exit_code == 1
    assert "Flagged: it didn't pass the checks" in result.stderr
    done = runner.invoke(cli, ["linkedin", "done"])
    assert done.exit_code == 0, done.output
    assert done.stdout.splitlines() == [
        "Marked 3 sections done.",
        "Not marked, as the checks flagged them: Position: Software Engineer at Northwind "
        "Traders. They're written again.",
    ]


def test_done_needs_a_pack() -> None:
    result = runner.invoke(cli, ["linkedin", "done"])
    assert result.exit_code == 1
    assert "write one with `wj linkedin`" in result.stderr


def test_linkedin_needs_the_llm_settings() -> None:
    result = linkedin()
    assert result.exit_code == 1
    assert "LLM_MODEL_STANDARD" in result.stderr
