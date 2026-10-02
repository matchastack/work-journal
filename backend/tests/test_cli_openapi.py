import json
from pathlib import Path

from typer.testing import CliRunner

from app.cli import cli


def test_wj_openapi_writes_the_schema_the_web_app_uses(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"
    result = CliRunner().invoke(cli, ["openapi", str(out)])
    assert result.exit_code == 0, result.output
    schema = json.loads(out.read_text())
    assert {"/healthz", "/auth/me", "/auth/logout"} <= schema["paths"].keys()
    assert schema["components"]["schemas"]["Me"]["required"] == ["id", "githubLogin"]


def test_wj_openapi_prints_the_schema_without_a_file() -> None:
    result = CliRunner().invoke(cli, ["openapi"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["info"]["title"] == "Work Journal"
