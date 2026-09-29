import json
from pathlib import Path

from typer.testing import CliRunner

from app.cli import LOCAL_DIR, cli
from app.schema.fact import Fact
from app.schema.profile import Profile

FIXTURE = Path(__file__).parent / "fixtures" / "master-resume.tex"
runner = CliRunner()


def test_import_tex_writes_the_profile_facts_and_report(tmp_path: Path) -> None:
    result = runner.invoke(cli, ["import", "tex", str(FIXTURE), "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Imported 2 roles" in result.stdout
    profile = Profile.model_validate_json((tmp_path / "profile.json").read_text(encoding="utf-8"))
    assert profile.basics.name == "Casey Morgan"
    facts = json.loads((tmp_path / "facts.json").read_text(encoding="utf-8"))
    assert len([Fact.model_validate(fact) for fact in facts]) == 9
    assert (tmp_path / "import-report.txt").read_text(encoding="utf-8").startswith("Imported")


def test_import_tex_keeps_an_earlier_import_unless_forced(tmp_path: Path) -> None:
    arguments = ["import", "tex", str(FIXTURE), "--out", str(tmp_path)]
    assert runner.invoke(cli, arguments).exit_code == 0
    again = runner.invoke(cli, arguments)
    assert again.exit_code == 1
    assert "use --force" in again.stderr
    assert runner.invoke(cli, [*arguments, "--force"]).exit_code == 0


def test_import_tex_explains_a_file_it_cannot_read(tmp_path: Path) -> None:
    source = tmp_path / "broken.tex"
    source.write_text("\\documentclass{article}", encoding="utf-8")
    result = runner.invoke(cli, ["import", "tex", str(source), "--out", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert "no \\begin{document}" in result.stderr
    assert not (tmp_path / "out").exists()


def test_the_default_output_folder_is_git_ignored() -> None:
    assert LOCAL_DIR.name == "local"
    ignored = (LOCAL_DIR.parent / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "local/" in ignored


def test_the_import_writes_no_en_dashes(tmp_path: Path) -> None:
    """LaTeX `--` and en dashes typed into the source both come out as hyphens."""
    source = tmp_path / "master-resume.tex"
    tex = FIXTURE.read_text(encoding="utf-8")
    assert "4--6 hours" in tex
    source.write_text(tex.replace("4--6 hours", "4\N{EN DASH}6 hours"), encoding="utf-8")
    result = runner.invoke(cli, ["import", "tex", str(source), "--out", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output
    for name in ("profile.json", "facts.json", "import-report.txt"):
        text = (tmp_path / "out" / name).read_text(encoding="utf-8")
        assert "\N{EN DASH}" not in text, name
    assert "4-6 hours" in (tmp_path / "out" / "facts.json").read_text(encoding="utf-8")
