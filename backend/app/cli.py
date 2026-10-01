"""The `wj` command-line tool. Later tasks add commands for rendering and tailoring."""

import asyncio
import json
import logging
from datetime import date
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
from app.importers.master_resume import MasterResumeError, import_master_resume
from app.jobs import run_worker
from app.main import create_app
from app.portfolio.build import Download, build_portfolio
from app.render.compile import CompileError
from app.render.fit import FitError
from app.render.resume import RenderError, render_resume
from app.schema.export import write_json_schemas
from app.schema.profile import Profile
from app.selection import MASTER_VARIANT, select

LOCAL_DIR = Path(__file__).resolve().parents[2] / "local"
"""The repository's git-ignored folder for personal data (`backend/app/cli.py` is two below)."""

cli = typer.Typer(help="Work Journal command-line tool.", no_args_is_help=True)
schema_cli = typer.Typer(help="JSON Schemas for the core data models.", no_args_is_help=True)
portfolio_cli = typer.Typer(help="The portfolio page.", no_args_is_help=True)
keys_cli = typer.Typer(help="The keys that encrypt journal and fact text.", no_args_is_help=True)
import_cli = typer.Typer(help="Import existing resume data.", no_args_is_help=True)
cli.add_typer(schema_cli, name="schema")
cli.add_typer(portfolio_cli, name="portfolio")
cli.add_typer(keys_cli, name="keys")
cli.add_typer(import_cli, name="import")


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


@cli.command()
def openapi(
    out: Annotated[
        Path | None, typer.Argument(help="File to write. Without one, prints the schema.")
    ] = None,
) -> None:
    """Write the API's OpenAPI schema, which `frontend/` turns into its API types."""
    schema = json.dumps(create_app().openapi(), indent=2, ensure_ascii=False) + "\n"
    if out is None:
        typer.echo(schema, nl=False)
        return
    out.write_text(schema, encoding="utf-8")
    typer.echo(f"Wrote {out}")


@cli.command()
def render(
    profile: Annotated[
        Path, typer.Option(help="The profile to render.", exists=True, dir_okay=False)
    ] = LOCAL_DIR / "profile.json",
    out: Annotated[
        Path,
        typer.Option(help="Where to write the PDF."),
    ] = LOCAL_DIR / "resumes" / "master.pdf",
) -> None:
    """Render the profile's full master document as a PDF. One-page resumes are tailored to a
    posting instead (OQ-6)."""
    loaded = Profile.model_validate_json(profile.read_text(encoding="utf-8"))
    selection = select(loaded, MASTER_VARIANT)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        rendered = render_resume(selection)
    except CompileError as error:
        log = out.with_suffix(".log")
        log.write_text(error.log, encoding="utf-8")
        typer.echo(f"{error}. The full log is in {log}.", err=True)
        raise typer.Exit(1) from None
    except (RenderError, FitError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    out.write_bytes(rendered.pdf)
    pages = f"{rendered.pages} page{'' if rendered.pages == 1 else 's'}"
    typer.echo(f"Wrote {out} ({pages}).")
    for note in selection.notes:
        typer.echo(f"Note: {note}")
    for warning in rendered.warnings:
        typer.echo(f"Warning: {warning}", err=True)


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
