import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import cli

PROFILE = Path(__file__).parent / "fixtures" / "profile.json"
ANSI_STYLE = re.compile(r"\x1b\[[0-9;]*m")
runner = CliRunner()


@pytest.mark.latex
def test_render_writes_the_master_document_as_a_pdf(tmp_path: Path) -> None:
    out = tmp_path / "resumes" / "master.pdf"
    result = runner.invoke(cli, ["render", "--profile", str(PROFILE), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_bytes().startswith(b"%PDF")
    assert f"Wrote {out} (1 page)." in result.stdout


def test_there_is_no_variant_to_choose() -> None:
    """OQ-6: only the master document is predefined; one-page resumes are tailored."""
    result = runner.invoke(cli, ["render", "--variant", "backend", "--profile", str(PROFILE)])
    assert result.exit_code == 2
    # In GitHub Actions, Typer colors the option name in usage errors.
    assert "No such option: --variant" in ANSI_STYLE.sub("", result.stderr)


def test_a_missing_profile_is_reported(tmp_path: Path) -> None:
    result = runner.invoke(cli, ["render", "--profile", str(tmp_path / "none.json")])
    assert result.exit_code == 2
    assert "does not exist" in result.stderr
