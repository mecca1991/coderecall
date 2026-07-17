"""Implementation scaffold for the init command."""

from __future__ import annotations

from pathlib import Path

import typer


def init_command(
    path: Path = typer.Option(
        Path(".coderecall.yml"),
        "--path",
        help="Path where the starter config will be written.",
    ),
) -> None:
    """Create a starter CodeRecall config file."""

    typer.echo("CodeRecall config initialization is scaffolded but not implemented yet.")
    typer.echo(f"Config path: {path}")
