"""Render expected application failures consistently across CLI commands."""

from __future__ import annotations

from typing import Never

import typer

from coderecall.core.errors import CodeRecallError


def exit_with_error(error: CodeRecallError) -> Never:
    """Render one actionable application error and stop the command."""

    typer.echo(f"Error: {error.message}", err=True)
    if error.recovery:
        typer.echo(error.recovery, err=True)
    if error.debug_details:
        typer.echo(f"Git details: {error.debug_details}", err=True)
    raise typer.Exit(code=1)
