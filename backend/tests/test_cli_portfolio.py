from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import cli

PROFILE = Path(__file__).parent / "fixtures" / "profile.json"
runner = CliRunner()


def test_build_writes_the_page(tmp_path: Path) -> None:
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4")
    out = tmp_path / "site"
    args = ["portfolio", "build", "--out", str(out), "--profile", str(PROFILE)]
    result = runner.invoke(cli, [*args, "--resume", f"Resume={resume}"])
    assert result.exit_code == 0, result.output
    assert f"Wrote {out / 'index.html'}" in result.stdout
    assert (out / "resumes" / "resume.pdf").exists()


@pytest.mark.parametrize("entry", ["no-label", "=resume.pdf", "Resume=missing.pdf"])
def test_a_resume_needs_a_label_and_a_file(tmp_path: Path, entry: str) -> None:
    args = ["portfolio", "build", "--out", str(tmp_path), "--profile", str(PROFILE)]
    result = runner.invoke(cli, [*args, "--resume", entry])
    assert result.exit_code == 2
    assert "Label=path/to/resume.pdf" in result.stderr
