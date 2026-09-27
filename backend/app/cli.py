"""The `wj` command-line tool. Later tasks add commands for import, rendering and tailoring."""

import typer

from app import __version__

cli = typer.Typer(help="Work Journal command-line tool.", no_args_is_help=True)


@cli.callback()
def main() -> None:
    """Work Journal command-line tool."""


@cli.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)
