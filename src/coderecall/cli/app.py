"""Top-level Typer application for the CodeRecall CLI."""

from __future__ import annotations

import typer

from coderecall import __version__
from coderecall.cli.commands.init import init_command
from coderecall.cli.commands.install_hook import install_hook_command
from coderecall.cli.commands.review import review_command

app = typer.Typer(
    name="coderecall",
    help="Check your understanding of a code change before review.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"coderecall {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the installed CodeRecall version.",
    ),
) -> None:
    """Run CodeRecall commands."""


app.command("review")(review_command)
app.command("install-hook")(install_hook_command)
app.command("init")(init_command)


def main() -> None:
    """Console script entry point."""

    app()
