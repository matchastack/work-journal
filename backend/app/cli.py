"""The `wj` command-line tool. Later tasks add commands for import, rendering and tailoring."""

import asyncio
import logging
from pathlib import Path
from typing import Annotated

import typer

from app import __version__
from app.config import get_settings
from app.jobs import run_worker
from app.schema.export import write_json_schemas

cli = typer.Typer(help="Work Journal command-line tool.", no_args_is_help=True)
schema_cli = typer.Typer(help="JSON Schemas for the core data models.", no_args_is_help=True)
cli.add_typer(schema_cli, name="schema")


@cli.callback()
def main() -> None:
    """Work Journal command-line tool."""


@cli.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@cli.command()
def worker(
    concurrency: Annotated[int, typer.Option(min=1, help="How many jobs to run at once.")] = 1,
) -> None:
    """Run the background worker: queued jobs and scheduled tasks, until Ctrl-C."""
    settings = get_settings()
    if settings.database_url is None:
        typer.echo("Set DATABASE_URL to the database that holds the jobs.", err=True)
        raise typer.Exit(1)
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run_worker(concurrency))


@schema_cli.command("export")
def export_schemas(
    out_dir: Annotated[Path, typer.Argument(help="Directory to write the schema files to.")],
) -> None:
    """Write one JSON Schema file per core model (profile, fact, variant, ...)."""
    for path in write_json_schemas(out_dir):
        typer.echo(f"Wrote {path}")
