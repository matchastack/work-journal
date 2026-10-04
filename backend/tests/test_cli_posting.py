import json
from pathlib import Path

import pytest
from anthropic.types.beta import BetaMessage
from typer.testing import CliRunner

from app import cli as cli_module
from app.cli import cli
from app.config import Settings
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, refusal, reply
from app.llm.usage import MemoryCallLog
from app.schema.jobs import JobPosting

POSTINGS = Path(__file__).parent / "fixtures" / "postings"
runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def use_fake(
    monkeypatch: pytest.MonkeyPatch, *replies: BetaMessage, model: str | None = "light-model"
) -> None:
    fake = FakeMessages(*replies)
    llm = LLMClient(Settings(llm_model_light=model), fake, MemoryCallLog())
    monkeypatch.setattr(cli_module, "_llm_client", lambda: llm)


def test_parse_prints_the_posting_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = JobPosting.model_validate_json((POSTINGS / "frontend.json").read_text())
    use_fake(monkeypatch, reply(expected))
    result = runner.invoke(cli, ["posting", "parse", str(POSTINGS / "frontend.txt")])
    assert result.exit_code == 0, result.output
    assert JobPosting.model_validate(json.loads(result.stdout)) == expected
    assert result.stderr == ""


def test_parse_reports_dropped_terms(monkeypatch: pytest.MonkeyPatch) -> None:
    posting = JobPosting(title="Data Engineer", must_have=("SQL", "Rust"))
    use_fake(monkeypatch, reply(posting))
    result = runner.invoke(cli, ["posting", "parse", str(POSTINGS / "data.txt")])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["mustHave"] == ["SQL"]
    assert 'Left out terms the posting doesn\'t contain: "Rust"' in result.stderr


def test_parse_needs_a_light_model(monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake(monkeypatch, model=None)
    result = runner.invoke(cli, ["posting", "parse", str(POSTINGS / "data.txt")])
    assert result.exit_code == 1
    assert "set LLM_MODEL_LIGHT" in result.stderr


def test_parse_reports_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake(monkeypatch, refusal("cyber"))
    result = runner.invoke(cli, ["posting", "parse", str(POSTINGS / "data.txt")])
    assert result.exit_code == 1
    assert "The posting couldn't be parsed: Claude declined the request (cyber)" in result.stderr
