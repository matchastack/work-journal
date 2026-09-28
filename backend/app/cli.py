"""The `wj` command-line tool. Later tasks add commands for import, rendering and tailoring."""

from pathlib import Path
from typing import Annotated

import typer

from app import __version__
from app.llm.client import LLMCallError, LLMClient, LLMConfigError
from app.schema.export import write_json_schemas
from app.tailoring.posting import parse_posting

cli = typer.Typer(help="Work Journal command-line tool.", no_args_is_help=True)
schema_cli = typer.Typer(help="JSON Schemas for the core data models.", no_args_is_help=True)
posting_cli = typer.Typer(help="Job postings to tailor resumes to.", no_args_is_help=True)
cli.add_typer(schema_cli, name="schema")
cli.add_typer(posting_cli, name="posting")


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


@schema_cli.command("export")
def export_schemas(
    out_dir: Annotated[Path, typer.Argument(help="Directory to write the schema files to.")],
) -> None:
    """Write one JSON Schema file per core model (profile, fact, variant, ...)."""
    for path in write_json_schemas(out_dir):
        typer.echo(f"Wrote {path}")


@posting_cli.command("parse")
def parse_posting_file(
    source: Annotated[
        Path, typer.Argument(help="The posting, as plain text.", exists=True, dir_okay=False)
    ],
) -> None:
    """Parse a job posting into its title, skills, responsibilities and key terms, as JSON.

    Calls the light-tier model. Skills and terms keep the posting's exact spellings.
    """
    try:
        parsed = parse_posting(source.read_text(encoding="utf-8"), _llm_client())
    except LLMConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    except LLMCallError as error:
        typer.echo(f"The posting couldn't be parsed: {error}", err=True)
        raise typer.Exit(1) from None
    typer.echo(parsed.posting.model_dump_json(indent=2, exclude_none=True))
    if parsed.dropped:
        dropped = ", ".join(f'"{term}"' for term in parsed.dropped)
        typer.echo(f"Left out terms the posting doesn't contain: {dropped}", err=True)
