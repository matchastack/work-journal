import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

import app.cli as cli_module
from app.cli import cli
from app.render.resume import RenderedResume
from app.selection import Selection

PROFILE = Path(__file__).parent / "fixtures" / "profile.json"
ANSI_STYLE = re.compile(r"\x1b\[[0-9;]*m")
runner = CliRunner()


@pytest.mark.latex
def test_render_writes_the_master_document_as_a_pdf(tmp_path: Path) -> None:
    out = tmp_path / "resumes" / "master.pdf"
    result = runner.invoke(cli, ["render", "--profile", str(PROFILE), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_bytes().startswith(b"%PDF")
    assert out.with_suffix(".tex").read_text(encoding="utf-8").count("\\documentclass") == 1
    assert f"Wrote {out} (1 page) and its source, {out.with_suffix('.tex')}." in result.stdout


def test_render_writes_the_tex_source_beside_the_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def render_resume(selection: Selection) -> RenderedResume:
        return RenderedResume(
            pdf=b"%PDF",
            tex="\\documentclass{article}",
            pages=2,
            text="",
            cuts=(),
            warnings=(),
            selection=selection,
        )

    monkeypatch.setattr(cli_module, "render_resume", render_resume)
    out = tmp_path / "resumes" / "master.pdf"
    tex = tmp_path / "resumes" / "master.tex"
    result = runner.invoke(cli, ["render", "--profile", str(PROFILE), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == b"%PDF"
    assert tex.read_text(encoding="utf-8") == "\\documentclass{article}"
    assert f"Wrote {out} (2 pages) and its source, {tex}." in result.stdout


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
