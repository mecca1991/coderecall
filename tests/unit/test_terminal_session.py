"""Tests for terminal session rendering and answer capture."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from coderecall.cli.terminal_session import TerminalSessionAdapter
from coderecall.core.types import (
    ChangedFile,
    DiffCollection,
    DiffSummary,
    EvidenceCitation,
    FileStatus,
    FilteredFile,
    FilterReason,
    LikelySideEffect,
    ModelMode,
    Question,
    QuestionCategory,
    RepositoryContext,
    SideEffectKind,
)


def make_question(question_id: str, category: QuestionCategory) -> Question:
    return Question(
        id=question_id,
        category=category,
        prompt=f"Prompt for {question_id}?",
        rationale="The changed code provides relevant evidence.",
    )


QUESTIONS = (
    make_question("behavior", QuestionCategory.BEHAVIOR),
    make_question("failure", QuestionCategory.FAILURE),
    make_question("evidence", QuestionCategory.EVIDENCE),
)

FOLLOW_UP = Question(
    id="failure-follow-up",
    category=QuestionCategory.FOLLOW_UP,
    prompt="The transaction cannot reverse the charge. How should reconciliation work?",
    rationale="A grounded failure gap remains.",
)


def test_privacy_disclosure_has_exact_plain_copy() -> None:
    output = StringIO()
    terminal = TerminalSessionAdapter(output_stream=output, plain=True)

    terminal.render_privacy_disclosure(ModelMode.LOCAL_HEURISTIC)

    assert output.getvalue() == (
        "Privacy\n"
        "Model mode: Local heuristic (no remote model)\n"
        "Repository content, answers, and reports stay on this machine.\n"
        "CodeRecall sends no telemetry and makes no network requests.\n"
        "\n"
    )


def test_capture_preserves_multiline_text_and_normalizes_terminal_line_endings() -> None:
    terminal = TerminalSessionAdapter(
        input_stream=StringIO("first line\r\nsecond\rline\n\n"),
        output_stream=StringIO(),
        plain=True,
    )

    answers = terminal.capture_answers(QUESTIONS[:1])

    assert answers[0].raw_text == "first line\nsecond\rline"
    assert answers[0].skipped is False


def test_immediate_empty_line_records_an_explicit_skip() -> None:
    terminal = TerminalSessionAdapter(
        input_stream=StringIO("\n"),
        output_stream=StringIO(),
        plain=True,
    )

    answers = terminal.capture_answers(QUESTIONS[:1])

    assert answers[0].raw_text == ""
    assert answers[0].skipped is True


def test_whitespace_only_lines_remain_answer_content() -> None:
    terminal = TerminalSessionAdapter(
        input_stream=StringIO("  \n\t\n\n"),
        output_stream=StringIO(),
        plain=True,
    )

    answers = terminal.capture_answers(QUESTIONS[:1])

    assert answers[0].raw_text == "  \n\t"
    assert answers[0].skipped is False


def test_eof_submits_partial_text_and_skips_remaining_questions() -> None:
    output = StringIO()
    terminal = TerminalSessionAdapter(
        input_stream=StringIO("partial\nstill partial"),
        output_stream=output,
        plain=True,
    )

    answers = terminal.capture_answers(QUESTIONS)

    assert tuple(answer.question_id for answer in answers) == (
        "behavior",
        "failure",
        "evidence",
    )
    assert answers[0].raw_text == "partial\nstill partial"
    assert answers[0].skipped is False
    assert all(answer.raw_text == "" for answer in answers[1:])
    assert all(answer.skipped is True for answer in answers[1:])
    assert "Answer recorded.\nEnd of input: 2 remaining questions skipped.\n" in output.getvalue()
    assert "partial" not in output.getvalue()


def test_injected_clock_records_stable_utc_timestamps_in_question_order() -> None:
    timestamps = iter(
        (
            datetime(2026, 7, 18, 10, 0, tzinfo=UTC),
            datetime(2026, 7, 18, 10, 1, tzinfo=UTC),
            datetime(2026, 7, 18, 10, 2, tzinfo=UTC),
        )
    )
    terminal = TerminalSessionAdapter(
        input_stream=StringIO("answer\n\n\nlast\n\n"),
        output_stream=StringIO(),
        clock=lambda: next(timestamps),
        plain=True,
    )

    answers = terminal.capture_answers(QUESTIONS)

    assert tuple(answer.question_id for answer in answers) == tuple(
        question.id for question in QUESTIONS
    )
    assert tuple(answer.timestamp for answer in answers) == (
        datetime(2026, 7, 18, 10, 0, tzinfo=UTC),
        datetime(2026, 7, 18, 10, 1, tzinfo=UTC),
        datetime(2026, 7, 18, 10, 2, tzinfo=UTC),
    )
    assert tuple(answer.skipped for answer in answers) == (False, True, False)


def test_capture_follow_up_records_an_answer_under_a_distinct_heading() -> None:
    output = StringIO()
    terminal = TerminalSessionAdapter(
        input_stream=StringIO("Use an idempotency record and reconcile pending charges.\n\n"),
        output_stream=output,
        plain=True,
    )

    answer = terminal.capture_follow_up(FOLLOW_UP)

    assert answer.question_id == "failure-follow-up"
    assert answer.raw_text == "Use an idempotency record and reconcile pending charges."
    assert answer.skipped is False
    assert output.getvalue() == (
        "\n"
        "Follow-up\n"
        "The transaction cannot reverse the charge. How should reconciliation work?\n"
        "Answer:\n"
        "Answer recorded.\n"
    )


def test_capture_follow_up_preserves_an_explicit_skip() -> None:
    output = StringIO()
    terminal = TerminalSessionAdapter(
        input_stream=StringIO("\n"),
        output_stream=output,
        plain=True,
    )

    answer = terminal.capture_follow_up(FOLLOW_UP)

    assert answer.question_id == "failure-follow-up"
    assert answer.raw_text == ""
    assert answer.skipped is True
    assert output.getvalue().endswith("Answer:\nSkipped.\n")


def test_capture_follow_up_preserves_an_eof_skip() -> None:
    output = StringIO()
    terminal = TerminalSessionAdapter(
        input_stream=StringIO(""),
        output_stream=output,
        plain=True,
    )

    answer = terminal.capture_follow_up(FOLLOW_UP)

    assert answer.question_id == "failure-follow-up"
    assert answer.raw_text == ""
    assert answer.skipped is True
    assert output.getvalue().endswith("Answer:\nSkipped.\n")


def test_plain_session_has_stable_readable_multiline_output() -> None:
    output = StringIO()
    terminal = TerminalSessionAdapter(
        input_stream=StringIO("It changes behavior.\n\n\n"),
        output_stream=output,
        plain=True,
    )
    repository = RepositoryContext(
        root=Path("/work/repository"),
        current_branch="feature/plain-output",
    )
    diff = DiffCollection(
        merge_base="1234567890abcdef",
        changed_files=(ChangedFile(path=Path("src/review.py"), status=FileStatus.MODIFIED),),
        filtered_files=(
            FilteredFile(
                path=Path("package-lock.json"),
                status=FileStatus.MODIFIED,
                reason=FilterReason.LOCKFILE,
            ),
        ),
    )
    summary = DiffSummary(
        purpose="Make review output easier to read.",
        relevant_files=(Path("src/review.py"),),
        tests=(Path("tests/test_review.py"),),
    )

    terminal.render_repository_context(repository, "main", diff)
    terminal.render_diff_summary(summary)
    answers = terminal.capture_answers(QUESTIONS[:2])
    terminal.render_answer_counts(answers)

    assert output.getvalue() == (
        "CodeRecall review\n"
        'Repository: "/work/repository"\n'
        "Branch: feature/plain-output -> main\n"
        "Merge base: 1234567890ab\n"
        "Changes: 2 total, 1 analyzed, 1 filtered\n"
        "Changed files:\n"
        '  - modified: "src/review.py"\n'
        "Filtered files:\n"
        '  - modified: "package-lock.json" (filtered: lockfile)\n'
        "\n"
        "Change summary\n"
        "Purpose: Make review output easier to read.\n"
        "Relevant files:\n"
        '  - "src/review.py"\n'
        "Tests found:\n"
        '  - "tests/test_review.py"\n'
        "\n"
        "Questions\n"
        "A blank line submits; press Enter immediately to skip.\n"
        "\n"
        "Question 1/2 — Behavior\n"
        "Prompt for behavior?\n"
        "Answer:\n"
        "Answer recorded.\n"
        "\n"
        "Question 2/2 — Failure\n"
        "Prompt for failure?\n"
        "Answer:\n"
        "Skipped.\n"
        "\n"
        "Session complete\n"
        "Answers: 1 answered, 1 skipped\n"
    )
    assert "It changes behavior." not in output.getvalue()
    assert "\x1b" not in output.getvalue()
    assert "[bold]" not in output.getvalue()


def test_summary_always_surfaces_language_limit_among_other_uncertainty_notes() -> None:
    output = StringIO()
    terminal = TerminalSessionAdapter(output_stream=output, plain=True)
    language_limit = (
        "Symbol-level analysis was unavailable for Dart (.dart); any symbols inferred from "
        "hunk context are heuristic."
    )
    summary = DiffSummary(
        purpose="Summary.",
        uncertainty_notes=(
            "The first unrelated note.",
            "The second unrelated note.",
            "The third unrelated note.",
            language_limit,
        ),
    )

    terminal.render_diff_summary(summary)

    assert language_limit in output.getvalue()


def test_stop_message_uses_an_explicit_heading() -> None:
    output = StringIO()
    terminal = TerminalSessionAdapter(output_stream=output, plain=True)

    terminal.render_stop_message("No meaningful files remain after filtering.")

    assert output.getvalue() == "Review stopped\nNo meaningful files remain after filtering.\n"


def test_paths_and_repository_values_are_escaped_for_terminal_output() -> None:
    output = StringIO()
    terminal = TerminalSessionAdapter(output_stream=output, plain=True)
    unsafe_path = Path("src/line\nbreak-\x1b[31m.py")
    repository = RepositoryContext(
        root=Path("/work/line\nbreak-\x1b[31m"),
        current_branch="feature/control\x1b",
    )
    diff = DiffCollection(
        merge_base="1234567890abcdef",
        changed_files=(
            ChangedFile(
                path=unsafe_path,
                status=FileStatus.RENAMED,
                old_path=Path("old\n.py"),
            ),
        ),
    )
    summary = DiffSummary(
        purpose="Review an escaped path.",
        relevant_files=(unsafe_path,),
        side_effects=(
            LikelySideEffect(
                kind=SideEffectKind.FILE_WRITE,
                description="The change may write to a local file.",
                evidence=(EvidenceCitation(kind="call", file_path=unsafe_path, symbol="open"),),
            ),
        ),
    )

    terminal.render_repository_context(repository, "main\nbranch", diff)
    terminal.render_diff_summary(summary)

    rendered = output.getvalue()
    assert "\x1b" not in rendered
    assert "src/line\nbreak" not in rendered
    assert "feature/control\\u001b -> main\\nbranch" in rendered
    assert '"old\\n.py" -> "src/line\\nbreak-\\u001b[31m.py"' in rendered


def test_forced_styling_adds_ansi_without_changing_semantic_output() -> None:
    plain_output = StringIO()
    styled_output = StringIO()
    plain_terminal = TerminalSessionAdapter(
        input_stream=StringIO("\n"),
        output_stream=plain_output,
        plain=True,
        force_terminal=True,
    )
    styled_terminal = TerminalSessionAdapter(
        input_stream=StringIO("\n"),
        output_stream=styled_output,
        force_terminal=True,
    )
    summary = DiffSummary(purpose="Explain the visible change.")

    plain_terminal.render_privacy_disclosure(ModelMode.LOCAL_HEURISTIC)
    plain_terminal.render_diff_summary(summary)
    plain_answers = plain_terminal.capture_answers(QUESTIONS[:1])
    plain_terminal.render_answer_counts(plain_answers)
    styled_terminal.render_privacy_disclosure(ModelMode.LOCAL_HEURISTIC)
    styled_terminal.render_diff_summary(summary)
    styled_answers = styled_terminal.capture_answers(QUESTIONS[:1])
    styled_terminal.render_answer_counts(styled_answers)

    styled = styled_output.getvalue()
    without_ansi = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", styled)
    assert "\x1b[" in styled
    assert without_ansi == plain_output.getvalue()
