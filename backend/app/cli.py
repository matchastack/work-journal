"""The `wj` command-line tool. Later tasks add commands for rendering and tailoring."""

import json
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from app import __version__
from app.importers.master_resume import MasterResumeError, import_master_resume
from app.portfolio.build import Download, build_portfolio
from app.schema.export import write_json_schemas
from app.schema.profile import Profile

LOCAL_DIR = Path(__file__).resolve().parents[2] / "local"
"""The repository's git-ignored folder for personal data (`backend/app/cli.py` is two below)."""

cli = typer.Typer(help="Work Journal command-line tool.", no_args_is_help=True)
schema_cli = typer.Typer(help="JSON Schemas for the core data models.", no_args_is_help=True)
portfolio_cli = typer.Typer(help="The portfolio page.", no_args_is_help=True)
import_cli = typer.Typer(help="Import existing resume data.", no_args_is_help=True)
cli.add_typer(schema_cli, name="schema")
cli.add_typer(portfolio_cli, name="portfolio")
cli.add_typer(import_cli, name="import")


@cli.callback()
def main() -> None:
    """Work Journal command-line tool."""


@cli.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@schema_cli.command("export")
def export_schemas(
    out_dir: Annotated[Path, typer.Argument(help="Directory to write the schema files to.")],
) -> None:
    """Write one JSON Schema file per core model (profile, fact, variant, ...)."""
    for path in write_json_schemas(out_dir):
        typer.echo(f"Wrote {path}")


@portfolio_cli.command("build")
def build_portfolio_page(
    out: Annotated[Path, typer.Option(help="The folder to write the page to.")],
    profile: Annotated[
        Path, typer.Option(help="The profile to publish.", exists=True, dir_okay=False)
    ] = LOCAL_DIR / "profile.json",
    site_url: Annotated[
        str | None, typer.Option(help="The page's public address, for link previews.")
    ] = None,
    resume: Annotated[
        list[str] | None,
        typer.Option(help='A resume to offer, as "Label=path/to/resume.pdf". Repeat for more.'),
    ] = None,
) -> None:
    """Build the portfolio page as static HTML from the profile's public content."""
    downloads: list[Download] = []
    for entry in resume or []:
        label, separator, path = entry.partition("=")
        if not separator or not label.strip() or not Path(path).is_file():
            raise typer.BadParameter(
                f"{entry!r} isn't Label=path/to/resume.pdf, with an existing file",
                param_hint="--resume",
            )
        downloads.append(Download(label=label.strip(), source=Path(path)))
    loaded = Profile.model_validate_json(profile.read_text(encoding="utf-8"))
    page = build_portfolio(
        loaded, out, year=date.today().year, site_url=site_url, downloads=downloads
    )
    typer.echo(f"Wrote {page}")


@import_cli.command("tex")
def import_tex(
    source: Annotated[
        Path,
        typer.Argument(help="The master resume (.tex).", exists=True, dir_okay=False),
    ],
    out_dir: Annotated[
        Path,
        typer.Option("--out", help="Folder for profile.json, facts.json and import-report.txt."),
    ] = LOCAL_DIR,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite the files of an earlier import.")
    ] = False,
) -> None:
    """Import a master resume written in LaTeX into files in the git-ignored `local/` folder.

    Nothing is sent to an LLM. The report lists what to check and what wasn't imported.
    """
    outputs = {name: out_dir / name for name in ("profile.json", "facts.json", "import-report.txt")}
    if not force and (existing := [path for path in outputs.values() if path.exists()]):
        typer.echo(f"{existing[0]} already exists; use --force to overwrite it.", err=True)
        raise typer.Exit(1)
    try:
        result = import_master_resume(source.read_text(encoding="utf-8"))
    except MasterResumeError as error:
        typer.echo(f"Can't import {source}: {error}", err=True)
        raise typer.Exit(1) from error

    out_dir.mkdir(parents=True, exist_ok=True)
    outputs["profile.json"].write_text(
        result.profile.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    facts = [fact.model_dump(mode="json") for fact in result.facts]
    outputs["facts.json"].write_text(
        json.dumps(facts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report = result.report.render()
    outputs["import-report.txt"].write_text(report, encoding="utf-8")
    typer.echo(report)
    for path in outputs.values():
        typer.echo(f"Wrote {path}")
