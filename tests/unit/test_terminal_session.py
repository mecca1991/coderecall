"""Tests for terminal answer capture."""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

from coderecall.cli.terminal_session import TerminalSessionAdapter
from coderecall.core.types import Question, QuestionCategory


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


def test_capture_preserves_multiline_text_and_normalizes_terminal_line_endings() -> None:
    terminal = TerminalSessionAdapter(
        input_stream=StringIO("first line\r\nsecond\rline\n\n"),
        output_stream=StringIO(),
    )

    answers = terminal.capture_answers(QUESTIONS[:1])

    assert answers[0].raw_text == "first line\nsecond\rline"
    assert answers[0].skipped is False


def test_immediate_empty_line_records_an_explicit_skip() -> None:
    terminal = TerminalSessionAdapter(
        input_stream=StringIO("\n"),
        output_stream=StringIO(),
    )

    answers = terminal.capture_answers(QUESTIONS[:1])

    assert answers[0].raw_text == ""
    assert answers[0].skipped is True


def test_whitespace_only_lines_remain_answer_content() -> None:
    terminal = TerminalSessionAdapter(
        input_stream=StringIO("  \n\t\n\n"),
        output_stream=StringIO(),
    )

    answers = terminal.capture_answers(QUESTIONS[:1])

    assert answers[0].raw_text == "  \n\t"
    assert answers[0].skipped is False


def test_eof_submits_partial_text_and_skips_remaining_questions() -> None:
    terminal = TerminalSessionAdapter(
        input_stream=StringIO("partial\nstill partial"),
        output_stream=StringIO(),
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


def test_rendering_is_plain_text_and_shows_the_instruction_once() -> None:
    output = StringIO()
    terminal = TerminalSessionAdapter(
        input_stream=StringIO("\n\n"),
        output_stream=output,
    )

    terminal.capture_answers(QUESTIONS[:2])

    rendered = output.getvalue()
    assert "[behavior]" in rendered
    assert "Prompt for behavior?" in rendered
    assert "[failure]" in rendered
    assert "Prompt for failure?" in rendered
    assert rendered.count("A blank line submits; press Enter immediately to skip.") == 1
    assert "\x1b" not in rendered
