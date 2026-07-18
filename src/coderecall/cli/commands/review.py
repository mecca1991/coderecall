"""Implementation of the review command."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from coderecall.analysis import (
    ChangeModelBuilder,
    DiffSummaryService,
    FileFilter,
    QuestionGenerator,
    SideEffectDetector,
)
from coderecall.cli.terminal_session import TerminalSessionAdapter
from coderecall.core.errors import CodeRecallError, QuestionGenerationUnavailable
from coderecall.core.types import ChangedFile, DiffSummary, FileStatus, FilteredFile
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


def _format_filtered_file(filtered_file: FilteredFile) -> str:
    status = filtered_file.status.value if filtered_file.status is not None else "changed"
    return (
        f"  {status}: {_format_path(filtered_file.path)} (filtered: {filtered_file.reason.value})"
    )


def _format_path(path: Path) -> str:
    return json.dumps(str(path), ensure_ascii=True)


def _render_diff_summary(summary: DiffSummary) -> tuple[str, ...]:
    lines = ["Diff summary", f"Purpose: {summary.purpose}"]
    if summary.relevant_files:
        lines.append("Relevant files:")
        lines.extend(f"  - {_format_path(path)}" for path in summary.relevant_files)
    if summary.tests:
        lines.append("Tests found:")
        lines.extend(f"  - {_format_path(path)}" for path in summary.tests[:5])
        if len(summary.tests) > 5:
            lines.append(f"  - and {len(summary.tests) - 5} more")
    if summary.side_effects:
        lines.append("Likely side effects:")
        for side_effect in summary.side_effects:
            evidence_paths = tuple(
                dict.fromkeys(citation.file_path for citation in side_effect.evidence)
            )
            evidence = ", ".join(_format_path(path) for path in evidence_paths[:3])
            lines.append(
                f"  - {side_effect.kind.value}: {side_effect.description} Evidence: {evidence}"
            )
    if summary.uncertainty_notes:
        lines.append("Uncertainty:")
        lines.extend(f"  - {note}" for note in summary.uncertainty_notes[:3])
        if len(summary.uncertainty_notes) > 3:
            lines.append(f"  - and {len(summary.uncertainty_notes) - 3} more notes")
    return tuple(lines)


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
        max=3,
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
        diff = DiffCollector(git, file_filter=FileFilter()).collect(
            repository,
            selected_base,
            include_uncommitted=include_uncommitted,
        )
    except CodeRecallError as error:
        _exit_with_error(error)

    context = ChangeModelBuilder(source_reader=git).build(repository, selected_base, diff)
    context = SideEffectDetector().detect(context)
    summary = DiffSummaryService().summarize(context)

    typer.echo("CodeRecall review context")
    typer.echo(f"Repository root: {_format_path(repository.root)}")
    typer.echo(f"Current branch: {repository.current_branch}")
    typer.echo(f"Base branch: {selected_base}")
    typer.echo(f"Merge base: {diff.merge_base[:12]}")
    typer.echo(f"Changed files: {len(diff.changed_files) + len(diff.filtered_files)}")
    typer.echo(f"Files for analysis: {len(diff.changed_files)}")
    for changed_file in diff.changed_files:
        typer.echo(_format_changed_file(changed_file))
    typer.echo(f"Filtered files: {len(diff.filtered_files)}")
    for filtered_file in diff.filtered_files:
        typer.echo(_format_filtered_file(filtered_file))
    for line in _render_diff_summary(summary):
        typer.echo(line)
    if not diff.changed_files:
        typer.echo("Review stopped: no meaningful files remain after filtering.")
        return

    try:
        generated_questions = QuestionGenerator().generate(context)
    except QuestionGenerationUnavailable:
        typer.echo("Review stopped: changed files contain no analyzable question evidence.")
        return

    selected_questions = generated_questions[:questions]
    answers = TerminalSessionAdapter().capture_answers(selected_questions)
    answered_count = sum(not answer.skipped for answer in answers)
    skipped_count = len(answers) - answered_count
    typer.echo(f"\nAnswers: {answered_count} answered, {skipped_count} skipped")
