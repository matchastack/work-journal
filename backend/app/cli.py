"""The `wj` command-line tool. Later tasks add commands for rendering and tailoring."""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import typer

from app import __version__
from app.extraction import extract_facts
from app.importers.master_resume import MasterResumeError, import_master_resume
from app.llm.client import LLMCallError, LLMClient, LLMConfigError
from app.schema.export import write_json_schemas
from app.schema.profile import Profile

LOCAL_DIR = Path(__file__).resolve().parents[2] / "local"
"""The repository's git-ignored folder for personal data (`backend/app/cli.py` is two below)."""

cli = typer.Typer(help="Work Journal command-line tool.", no_args_is_help=True)
schema_cli = typer.Typer(help="JSON Schemas for the core data models.", no_args_is_help=True)
import_cli = typer.Typer(help="Import existing resume data.", no_args_is_help=True)
cli.add_typer(schema_cli, name="schema")
cli.add_typer(import_cli, name="import")


def _llm_client() -> LLMClient:
    """The Claude client the commands use (tests replace it with a fake)."""
    return LLMClient.from_settings()


@cli.callback()
def main() -> None:
    """Work Journal command-line tool."""


@cli.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@cli.command()
def extract(
    note: Annotated[
        Path, typer.Argument(help="The journal note, as plain text.", exists=True, dir_okay=False)
    ],
    profile: Annotated[
        Path, typer.Option(help="The profile the facts link to.", exists=True, dir_okay=False)
    ] = LOCAL_DIR / "profile.json",
    written: Annotated[
        datetime | None,
        typer.Option(
            "--date", formats=["%Y-%m-%d"], help="The day the note was written. Default: today."
        ),
    ] = None,
) -> None:
    """Extract the facts in a journal note and print them as JSON.

    Calls the standard-tier model. A fact with a number the note doesn't give is left out.
    """
    try:
        extraction = extract_facts(
            note.read_text(encoding="utf-8"),
            Profile.model_validate_json(profile.read_text(encoding="utf-8")),
            _llm_client(),
            written=written.date() if written else date.today(),
        )
    except LLMConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    except LLMCallError as error:
        typer.echo(f"The note couldn't be read: {error}", err=True)
        raise typer.Exit(1) from None
    facts = [fact.model_dump(mode="json", exclude_none=True) for fact in extraction.facts]
    typer.echo(json.dumps(facts, indent=2, ensure_ascii=False))
    for dropped in extraction.dropped:
        reasons = "; ".join(dropped.reasons)
        typer.echo(f'Left out "{dropped.statement}": {reasons}', err=True)
    for message in extraction.notes:
        typer.echo(f"Note: {message}", err=True)


@schema_cli.command("export")
def export_schemas(
    out_dir: Annotated[Path, typer.Argument(help="Directory to write the schema files to.")],
) -> None:
    """Write one JSON Schema file per core model (profile, fact, variant, ...)."""
    for path in write_json_schemas(out_dir):
        typer.echo(f"Wrote {path}")


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
