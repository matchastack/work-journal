from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import cli

PROFILE = Path(__file__).parent / "fixtures" / "profile.json"
runner = CliRunner()


@pytest.mark.latex
def test_render_writes_the_variant_as_a_pdf(tmp_path: Path) -> None:
    out = tmp_path / "resumes" / "backend.pdf"
    args = ["render", "--variant", "backend", "--profile", str(PROFILE), "--out", str(out)]
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert out.read_bytes().startswith(b"%PDF")
    assert f"Wrote {out} (1 page)." in result.stdout


def test_an_unknown_variant_lists_the_choices(tmp_path: Path) -> None:
    args = [
        "render",
        "--variant",
        "sales",
        "--profile",
        str(PROFILE),
        "--out",
        str(tmp_path / "x.pdf"),
    ]
    result = runner.invoke(cli, args)
    assert result.exit_code == 1
    assert "No variant 'sales'. Choose from: master, backend, data." in result.stderr
    assert not (tmp_path / "x.pdf").exists()


def test_a_missing_profile_is_reported(tmp_path: Path) -> None:
    result = runner.invoke(cli, ["render", "--profile", str(tmp_path / "none.json")])
    assert result.exit_code == 2
    assert "does not exist" in result.stderr
