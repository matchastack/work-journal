"""The `wj` command-line tool. Later tasks add commands for import, rendering and tailoring."""

from pathlib import Path
from typing import Annotated

import typer

from app import __version__
from app.render.compile import CompileError
from app.render.fit import FitError
from app.render.resume import RenderError, render_resume
from app.schema.export import write_json_schemas
from app.schema.profile import Profile
from app.selection import default_variants, select

LOCAL_DIR = Path(__file__).resolve().parents[2] / "local"
"""The repository's git-ignored folder for personal data (`backend/app/cli.py` is two below)."""

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
def render(
    variant: Annotated[
        str, typer.Option(help="The variant: master, or a role type such as backend.")
    ] = "master",
    profile: Annotated[
        Path, typer.Option(help="The profile to render.", exists=True, dir_okay=False)
    ] = LOCAL_DIR / "profile.json",
    out: Annotated[
        Path | None,
        typer.Option(help="Where to write the PDF.", show_default="local/resumes/<variant>.pdf"),
    ] = None,
) -> None:
    """Render a resume variant of the profile as a PDF, cut to fit its page limit."""
    loaded = Profile.model_validate_json(profile.read_text(encoding="utf-8"))
    variants = {choice.id: choice for choice in default_variants(loaded)}
    if variant not in variants:
        typer.echo(f"No variant {variant!r}. Choose from: {', '.join(variants)}.", err=True)
        raise typer.Exit(1)
    selection = select(loaded, variants[variant])
    path = out or LOCAL_DIR / "resumes" / f"{variant}.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        rendered = render_resume(selection)
    except CompileError as error:
        log = path.with_suffix(".log")
        log.write_text(error.log, encoding="utf-8")
        typer.echo(f"{error}. The full log is in {log}.", err=True)
        raise typer.Exit(1) from None
    except (RenderError, FitError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    path.write_bytes(rendered.pdf)
    pages = f"{rendered.pages} page{'' if rendered.pages == 1 else 's'}"
    typer.echo(f"Wrote {path} ({pages}).")
    for note in selection.notes:
        typer.echo(f"Note: {note}")
    if rendered.cuts:
        typer.echo(f"Cut to fit the {selection.max_pages}-page limit:")
        for cut in rendered.cuts:
            typer.echo(f"- {cut.kind} {cut.id}: {cut.text}")
    for warning in rendered.warnings:
        typer.echo(f"Warning: {warning}", err=True)


@schema_cli.command("export")
def export_schemas(
    out_dir: Annotated[Path, typer.Argument(help="Directory to write the schema files to.")],
) -> None:
    """Write one JSON Schema file per core model (profile, fact, variant, ...)."""
    for path in write_json_schemas(out_dir):
        typer.echo(f"Wrote {path}")
