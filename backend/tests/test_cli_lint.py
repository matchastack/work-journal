import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from app.cli import cli

PROFILE = Path(__file__).parent / "fixtures" / "profile.json"
runner = CliRunner()


def write_profile(tmp_path: Path, change: Callable[[dict[str, Any]], object]) -> Path:
    data = json.loads(PROFILE.read_text(encoding="utf-8"))
    change(data)
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_lint_passes_the_fictional_profile() -> None:
    result = runner.invoke(cli, ["lint", "--profile", str(PROFILE)])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "Not checked by code:",
        "  education[springfield_state].rules: rule 1 isn't checked: "
        '"Show the minor after the major."',
        "No problems found.",
    ]


def test_lint_fails_when_there_are_errors(tmp_path: Path) -> None:
    def change(data: dict[str, Any]) -> None:
        data["work"][0]["highlights"][0]["text"] = "Cut hosting costs by TODO."

    result = runner.invoke(cli, ["lint", "--profile", str(write_profile(tmp_path, change))])
    assert result.exit_code == 1
    lines = result.stdout.splitlines()
    assert lines[0] == (
        'error    R2          work[northwind].highlights[nw_orders]: placeholder text "TODO"'
    )
    assert lines[-1] == "1 error, 0 warnings."


def test_lint_passes_when_there_are_only_warnings(tmp_path: Path) -> None:
    def change(data: dict[str, Any]) -> None:
        data["projects"][0]["keywords"].append("Docker")

    result = runner.invoke(cli, ["lint", "--profile", str(write_profile(tmp_path, change))])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[-1] == "0 errors, 1 warning."


def test_a_missing_profile_is_reported(tmp_path: Path) -> None:
    result = runner.invoke(cli, ["lint", "--profile", str(tmp_path / "none.json")])
    assert result.exit_code == 2
    assert "does not exist" in result.stderr
