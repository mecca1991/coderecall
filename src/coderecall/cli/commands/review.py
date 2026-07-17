"""Implementation scaffold for the review command."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from coderecall.core.errors import CodeRecallError
from coderecall.core.types import ChangedFile, FileStatus
from coderecall.git import DiffCollector, GitAdapter


def _exit_with_error(error: CodeRecallError) -> None:
    typer.echo(f"Error: {error.message}", err=True)
    if error.recovery:
        typer.echo(error.recovery, err=True)
    if error.debug_details:
        typer.echo(f"Git details: {error.debug_details}", err=True)
    raise typer.Exit(code=1)


def _format_changed_file(changed_file: ChangedFile) -> str:
    if changed_file.status is FileStatus.RENAMED and changed_file.old_path is not None:
        path = f"{_format_path(changed_file.old_path)} -> {_format_path(changed_file.path)}"
    else:
        path = _format_path(changed_file.path)
    binary_suffix = " (binary)" if changed_file.is_binary else ""
    return f"  {changed_file.status.value}: {path}{binary_suffix}"


def _format_path(path: Path) -> str:
    return json.dumps(str(path), ensure_ascii=True)


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
        help="Include staged and unstaged changes to tracked files.",
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
        diff = DiffCollector(git).collect(
            repository,
            selected_base,
            include_uncommitted=include_uncommitted,
        )
    except CodeRecallError as error:
        _exit_with_error(error)

    typer.echo("CodeRecall review context")
    typer.echo(f"Repository root: {_format_path(repository.root)}")
    typer.echo(f"Current branch: {repository.current_branch}")
    typer.echo(f"Base branch: {selected_base}")
    typer.echo(f"Merge base: {diff.merge_base[:12]}")
    typer.echo(f"Changed files: {len(diff.changed_files)}")
    for changed_file in diff.changed_files:
        typer.echo(_format_changed_file(changed_file))
    for note in diff.uncertainty_notes:
        typer.echo(f"Note: {note}")
    typer.echo(f"Report path: {report}")
    typer.echo(f"Questions: {questions}")
    typer.echo(f"Follow-up enabled: {not no_follow_up}")
    typer.echo(f"Include uncommitted changes: {include_uncommitted}")
    typer.echo(f"Plain output: {plain}")
    typer.echo("Question and report generation are not implemented yet.")
