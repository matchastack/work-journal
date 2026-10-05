import json
from pathlib import Path

import pytest
from anthropic.types.beta import BetaMessage
from typer.testing import CliRunner, Result

from app import cli as cli_module
from app.cli import cli
from app.config import Settings
from app.extraction import ExtractionReply
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, refusal, reply
from app.llm.usage import MemoryCallLog

FIXTURES = Path(__file__).parent / "fixtures"
NOTE = FIXTURES / "notes" / "export.txt"
EXPORT_FACT = {
    "statement": "Cut the nightly export from about 50 to 12 minutes.",
    "kind": "accomplishment",
    "metrics": [
        {
            "subject": "nightly export duration",
            "kind": "change",
            "numbers": [50, 12],
            "unit": "minutes",
            "qualifier": "approximately",
        }
    ],
    "roleId": "northwind",
}
runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def use_fake(
    monkeypatch: pytest.MonkeyPatch, *replies: BetaMessage, model: str | None = "standard-model"
) -> FakeMessages:
    fake = FakeMessages(*replies)
    llm = LLMClient(Settings(llm_model_standard=model), fake, MemoryCallLog())
    monkeypatch.setattr(cli_module, "_llm_client", lambda: llm)
    return fake


def facts_reply(*facts: dict[str, object]) -> BetaMessage:
    return reply(ExtractionReply.model_validate({"facts": facts}))


def extract(*options: str) -> Result:
    profile = FIXTURES / "profile.json"
    return runner.invoke(cli, ["extract", str(NOTE), "--profile", str(profile), *options])


def test_extract_prints_the_facts_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use_fake(monkeypatch, facts_reply(EXPORT_FACT))
    result = extract("--date", "2026-09-14")
    assert result.exit_code == 0, result.output
    facts = json.loads(result.stdout)
    assert [(fact["id"], fact["roleId"], fact["origin"]) for fact in facts] == [
        ("fact_1", "northwind", "journal")
    ]
    assert fake.requests[0]["messages"][0]["content"].startswith(
        "The note was written on 2026-09-14."
    )
    assert result.stderr == ""


def test_extract_reports_what_it_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    computed = {**EXPORT_FACT, "statement": "Cut the nightly export by 76%.", "metrics": []}
    use_fake(monkeypatch, facts_reply(computed, {**EXPORT_FACT, "tools": ["Kafka"]}))
    result = extract()
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)) == 1
    assert result.stderr.splitlines() == [
        'Left out "Cut the nightly export by 76%.": the note doesn\'t give 76%',
        'Note: fact_1: removed the tool "Kafka", which the note doesn\'t name',
    ]


def test_extract_names_the_missing_model_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake(monkeypatch, model=None)
    result = extract()
    assert result.exit_code == 1
    assert "set LLM_MODEL_STANDARD" in result.stderr


def test_extract_reports_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake(monkeypatch, refusal())
    result = extract()
    assert result.exit_code == 1
    assert result.stderr.startswith("The note couldn't be read: Claude declined the request")


def test_a_date_in_another_format_is_rejected() -> None:
    assert extract("--date", "14/09/2026").exit_code == 2
