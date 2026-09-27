from typer.testing import CliRunner

from app import __version__
from app.cli import cli

runner = CliRunner()


def test_version_prints_installed_version() -> None:
    result = runner.invoke(cli, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_help_lists_commands() -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "version" in result.stdout
