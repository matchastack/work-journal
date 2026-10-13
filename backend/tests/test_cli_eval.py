from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from anthropic.types.beta import BetaMessage
from typer.testing import CliRunner, Result

from app import cli as cli_module
from app.cli import cli
from app.config import Settings, get_settings
from app.extraction import ExtractedFact, ExtractedMetric, ExtractionReply
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, reply
from app.llm.usage import CallLog

FIXTURES = Path(__file__).parent / "fixtures"
NOTE = "finally got the nightly export down from ~50 min to 12!! batching the db writes did it."
EXTRACTION = f"""
profile = "{FIXTURES / "profile.json"}"
written = 2026-09-14

[[cases]]
id = "a work note"
note = "{NOTE}"

[[cases.expected]]
terms = ["export"]
numbers = [50, 12]
"""
EXPORT = ExtractedFact(
    statement="Cut the nightly export from about 50 to 12 minutes by batching database writes.",
    kind="accomplishment",
    metrics=(
        ExtractedMetric(
            subject="nightly export duration",
            kind="change",
            numbers=(50, 12),
            unit="minutes",
            qualifier="approximately",
        ),
    ),
)
RECOVERED = reply(ExtractionReply(facts=(EXPORT,)))
MISSED = reply(ExtractionReply(facts=()))
runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fictional(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    folder = tmp_path / "evals"
    folder.mkdir()
    (folder / "extraction.toml").write_text(EXTRACTION, encoding="utf-8")
    monkeypatch.setattr(cli_module, "FICTIONAL_DIR", folder)
    return folder


def use_fake(monkeypatch: pytest.MonkeyPatch, *replies: BetaMessage) -> FakeMessages:
    fake = FakeMessages(*replies)
    settings = Settings(llm_model_standard="standard-model", llm_price_standard=(3, 15))

    def client(log: CallLog) -> LLMClient:
        return LLMClient(settings, fake, log)

    monkeypatch.setattr(cli_module, "_eval_client", client)
    return fake


def evaluate(*arguments: str) -> Result:
    return runner.invoke(cli, ["eval", *arguments])


@pytest.mark.usefixtures("fictional")
def test_eval_prints_the_report_and_each_case_as_it_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake(monkeypatch, RECOVERED)
    result = evaluate("extraction")
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(
        f"# Evaluation report\n\nThe fictional cases, run on {date.today().isoformat()}.\n"
    )
    assert "**Meets the bar.**" in result.stdout
    assert "| extraction | 1/1 | 0 | 0 | 1/1 (100%) | 1 | 0.0006 |" in result.stdout
    assert result.stderr == "extraction: a work note: pass\n"


@pytest.mark.usefixtures("fictional")
def test_eval_exits_with_1_when_the_bar_is_missed(monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake(monkeypatch, MISSED)
    result = evaluate("extraction")
    assert result.exit_code == 1
    assert "**Doesn't meet the bar:** fact recall 0%." in result.stdout
    assert result.stderr == "extraction: a work note: fail\n"


def test_eval_can_write_the_report_to_a_file(
    monkeypatch: pytest.MonkeyPatch, fictional: Path
) -> None:
    use_fake(monkeypatch, RECOVERED)
    out = fictional / "reports" / "baseline.md"
    result = evaluate("extraction", "--out", str(out))
    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8") == result.stdout
    assert result.stderr.endswith(f"Wrote {out}\n")


@pytest.mark.usefixtures("fictional")
def test_all_runs_every_suite_the_folder_has(monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake(monkeypatch, RECOVERED)
    result = evaluate("all")
    assert result.exit_code == 0, result.output
    assert "## extraction" in result.stdout
    assert "## writing" not in result.stdout


def test_private_runs_the_cases_in_local_evals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local = tmp_path / "local"
    (local / "evals").mkdir(parents=True)
    (local / "evals" / "extraction.toml").write_text(EXTRACTION, encoding="utf-8")
    monkeypatch.setattr(cli_module, "LOCAL_DIR", local)
    monkeypatch.setattr(cli_module, "FICTIONAL_DIR", tmp_path / "nothing")
    use_fake(monkeypatch, RECOVERED)
    result = evaluate("extraction", "--private")
    assert result.exit_code == 0, result.output
    assert "Your private cases (`local/evals/`), run on" in result.stdout


@pytest.mark.usefixtures("fictional")
def test_a_suite_without_cases_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake(monkeypatch)
    result = evaluate("writing")
    assert result.exit_code == 1
    assert "has no cases for writing; backend/evals/README.md describes the format." in (
        result.stderr
    )


def test_a_dataset_error_stops_before_any_call(
    monkeypatch: pytest.MonkeyPatch, fictional: Path
) -> None:
    (fictional / "writing.toml").write_text("cases = [", encoding="utf-8")
    fake = use_fake(monkeypatch)
    result = evaluate("all")
    assert result.exit_code == 1
    assert "writing.toml isn't valid TOML" in result.stderr
    assert fake.requests == []


@pytest.mark.usefixtures("fictional")
def test_eval_needs_a_key_and_the_standard_model() -> None:
    result = evaluate("extraction")
    assert result.exit_code == 1
    assert result.stderr == "Set ANTHROPIC_API_KEY and LLM_MODEL_STANDARD to run the evaluation.\n"
