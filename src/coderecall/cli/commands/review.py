"""Implementation scaffold for the review command."""

from __future__ import annotations

from pathlib import Path

import typer

from coderecall.core.errors import CodeRecallError
from coderecall.git import GitAdapter


def _exit_with_error(error: CodeRecallError) -> None:
    typer.echo(f"Error: {error.message}", err=True)
    if error.recovery:
        typer.echo(error.recovery, err=True)
    if error.debug_details:
        typer.echo(f"Git details: {error.debug_details}", err=True)
    raise typer.Exit(code=1)


def review_command(
    base: str | None = typer.Option(
        None,
        "--base",
        help="Base branch to compare against.",
    ),
    report: Path = typer.Option(
        Path("coderecall-report.md"),
        "--report",
        help="Output path for the local Markdown report.",
    ),
    questions: int = typer.Option(
        3,
        "--questions",
        min=1,
        help="Number of questions to ask.",
    ),
    no_follow_up: bool = typer.Option(
        False,
        "--no-follow-up",
        help="Disable the adaptive follow-up question.",
    ),
    include_uncommitted: bool = typer.Option(
        False,
        "--include-uncommitted",
        help="Include working tree changes in the review context.",
    ),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Disable styled terminal output.",
    ),
) -> None:
    """Run an understanding check against the current Git branch."""

    git = GitAdapter()
    try:
        repository = git.detect_repository()
        selected_base = git.select_base_branch(repository, base)
    except CodeRecallError as error:
        _exit_with_error(error)

    typer.echo("CodeRecall review is scaffolded but not implemented yet.")
    typer.echo(f"Repository root: {repository.root}")
    typer.echo(f"Current branch: {repository.current_branch}")
    typer.echo(f"Base branch: {selected_base}")
    typer.echo(f"Report path: {report}")
    typer.echo(f"Questions: {questions}")
    typer.echo(f"Follow-up enabled: {not no_follow_up}")
    typer.echo(f"Include uncommitted changes: {include_uncommitted}")
    typer.echo(f"Plain output: {plain}")
