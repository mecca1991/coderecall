"""Implementation of the review command."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import typer

from coderecall.analysis import (
    ChangeModelBuilder,
    DiffSummaryService,
    FileFilter,
    QuestionGenerator,
    SideEffectDetector,
)
from coderecall.cli.error_rendering import exit_with_error
from coderecall.cli.terminal_session import TerminalSessionAdapter
from coderecall.config import ConfigLoader, resolve_review_options
from coderecall.core.errors import (
    CodeRecallError,
    DocumentationOnlyChanges,
    QuestionGenerationUnavailable,
)
from coderecall.core.types import ModelMode
from coderecall.evaluation import FollowUpSelector, HeuristicEvaluator
from coderecall.git import DiffCollector, GitAdapter
from coderecall.reporting import (
    MarkdownReportWriter,
    ReportBuilder,
    ReviewTalkingPointGenerator,
)


def review_command(
    base: str | None = typer.Option(
        None,
        "--base",
        help="Base branch to compare against.",
    ),
    report: Path | None = typer.Option(
        None,
        "--report",
        help="Output path for the local Markdown report.",
    ),
    questions: int | None = typer.Option(
        None,
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
    include_uncommitted: bool | None = typer.Option(
        None,
        "--include-uncommitted/--no-include-uncommitted",
        help="Include staged and unstaged changes to tracked files.",
    ),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Disable styled terminal output.",
    ),
) -> None:
    """Run an understanding check against the current Git branch."""

    model_mode = ModelMode.LOCAL_HEURISTIC
    terminal = TerminalSessionAdapter(plain=plain)
    terminal.render_privacy_disclosure(model_mode)
    invocation_directory = Path.cwd().resolve()
    git = GitAdapter(invocation_directory)
    try:
        repository = git.detect_repository()
        config = ConfigLoader().load(repository.root)
        options = resolve_review_options(
            config=config,
            repository_root=repository.root,
            invocation_directory=invocation_directory,
            base=base,
            report_path=report,
            questions=questions,
            include_uncommitted=include_uncommitted,
        )
        selected_base = git.select_base_branch(repository, options.base)
        diff = DiffCollector(
            git,
            file_filter=FileFilter(configured_exclusions=options.exclude),
        ).collect(
            repository,
            selected_base,
            include_uncommitted=options.include_uncommitted,
        )
    except CodeRecallError as error:
        exit_with_error(error)

    context = ChangeModelBuilder(source_reader=git).build(repository, selected_base, diff)
    context = SideEffectDetector().detect(context)
    summary = DiffSummaryService().summarize(context)

    terminal.render_repository_context(repository, selected_base, diff)
    terminal.render_diff_summary(summary)
    if not diff.changed_files:
        terminal.render_stop_message("No meaningful files remain after filtering.")
        return

    try:
        generated_questions = QuestionGenerator().generate(context)
    except DocumentationOnlyChanges as error:
        terminal.render_stop_message(error.message)
        return
    except QuestionGenerationUnavailable:
        terminal.render_stop_message("Changed files contain no analyzable question evidence.")
        return

    selected_questions = generated_questions[: options.questions]
    answers = terminal.capture_answers(selected_questions)
    evaluator = HeuristicEvaluator()
    assessments = tuple(
        evaluator.evaluate(context, question, answer)
        for question, answer in zip(selected_questions, answers, strict=True)
    )
    review_talking_points = ReviewTalkingPointGenerator().generate(
        summary,
        selected_questions,
        assessments,
    )
    follow_up = FollowUpSelector().select(
        context,
        selected_questions,
        assessments,
        enabled=not no_follow_up,
    )
    all_answers = list(answers)
    if follow_up is not None:
        follow_up_answer = terminal.capture_follow_up(follow_up.question)
        follow_up = replace(follow_up, answer=follow_up_answer)
        all_answers.append(follow_up_answer)

    terminal.render_answer_counts(all_answers)
    report_path = options.report_path.resolve()
    built_report = ReportBuilder().build(
        context,
        summary,
        selected_questions,
        answers,
        assessments,
        model_mode=model_mode,
        follow_up=follow_up,
        review_talking_points=review_talking_points,
    )
    try:
        written_path = MarkdownReportWriter().write(built_report, report_path)
    except CodeRecallError as error:
        exit_with_error(error)
    terminal.render_report_written(written_path)
