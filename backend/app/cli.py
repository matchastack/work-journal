"""The `wj` command-line tool. Later tasks add commands for import, rendering and tailoring."""

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from app import __version__
from app.config import get_settings
from app.db.crypto import (
    DecryptionError,
    EncryptionKeyError,
    key_ring,
    new_key,
    rotate_keys,
)
from app.db.engine import create_engine, session_factory
from app.db.models import Base
from app.schema.export import write_json_schemas

cli = typer.Typer(help="Work Journal command-line tool.", no_args_is_help=True)
schema_cli = typer.Typer(help="JSON Schemas for the core data models.", no_args_is_help=True)
keys_cli = typer.Typer(help="The keys that encrypt journal and fact text.", no_args_is_help=True)
cli.add_typer(schema_cli, name="schema")
cli.add_typer(keys_cli, name="keys")


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


@keys_cli.command("new")
def new_encryption_key(
    key_id: Annotated[str, typer.Option("--id", help="A short name for the key, e.g. k2.")] = "k1",
) -> None:
    """Print a new key for DATA_ENCRYPTION_KEY. Store it only there."""
    try:
        typer.echo(new_key(key_id))
    except EncryptionKeyError as error:
        raise typer.BadParameter(str(error), param_hint="--id") from None


@keys_cli.command("rotate")
def rotate_encryption_keys() -> None:
    """Re-encrypt stored text with the first key in DATA_ENCRYPTION_KEY."""
    url = get_settings().database_url
    if url is None:
        typer.echo("Set DATABASE_URL to the database to rotate.", err=True)
        raise typer.Exit(1)
    try:
        current = key_ring().current_id
        counts = asyncio.run(_rotate(url.get_secret_value()))
    except (EncryptionKeyError, DecryptionError) as error:
        typer.echo(f"Nothing was changed: {error}", err=True)
        raise typer.Exit(1) from None
    for column, count in counts.items():
        typer.echo(f"{column}: re-encrypted {count} value{'' if count == 1 else 's'}")
    if not counts:
        typer.echo("There are no encrypted columns yet.")
    typer.echo(f"Everything is under key {current!r}. Older keys can leave DATA_ENCRYPTION_KEY.")


async def _rotate(url: str) -> dict[str, int]:
    engine = create_engine(url)
    try:
        async with session_factory(engine)() as session, session.begin():
            return await rotate_keys(session, Base.metadata)
    finally:
        await engine.dispose()
