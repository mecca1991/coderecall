"""Terminal rendering and answer capture for a review session."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from rich.console import Console
from rich.text import Text

from coderecall.core.types import (
    Answer,
    ChangedFile,
    DiffCollection,
    DiffSummary,
    FileStatus,
    FilteredFile,
    ModelMode,
    Question,
    RepositoryContext,
)

_HEADING_STYLE = "bold"
_CATEGORY_STYLE = "bold cyan"
_WARNING_STYLE = "yellow"
_STATUS_STYLE = "green"


class TerminalSessionAdapter:
    """Render a review session and return ordered, structured answers."""

    def __init__(
        self,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
        clock: Callable[[], datetime] | None = None,
        *,
        plain: bool = False,
        force_terminal: bool | None = None,
    ) -> None:
        self._input = input_stream if input_stream is not None else sys.stdin
        self._output = output_stream if output_stream is not None else sys.stdout
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._plain = plain
        self._console = Console(
            file=self._output,
            force_terminal=False if plain else force_terminal,
            color_system=None if plain else "auto",
            highlight=False,
            markup=False,
        )

    def render_privacy_disclosure(self, model_mode: ModelMode) -> None:
        """Disclose local review privacy behavior before repository inspection."""

        self._print("Privacy", style=_HEADING_STYLE)
        self._print(f"Model mode: {model_mode.value}")
        self._print("Repository content, answers, and reports stay on this machine.")
        self._print("CodeRecall sends no telemetry and makes no network requests.")
        self._print()

    def render_repository_context(
        self,
        repository: RepositoryContext,
        base_branch: str,
        diff: DiffCollection,
    ) -> None:
        """Render repository and diff metadata without relying on color."""

        total = len(diff.changed_files) + len(diff.filtered_files)
        self._print("CodeRecall review", style=_HEADING_STYLE)
        self._print(f"Repository: {self._format_path(repository.root)}")
        self._print(
            "Branch: "
            f"{self._escape_value(repository.current_branch)} -> "
            f"{self._escape_value(base_branch)}"
        )
        self._print(f"Merge base: {self._escape_value(diff.merge_base[:12])}")
        self._print(
            f"Changes: {total} total, {len(diff.changed_files)} analyzed, "
            f"{len(diff.filtered_files)} filtered"
        )
        if diff.changed_files:
            self._print("Changed files:")
            for changed_file in diff.changed_files:
                self._print(f"  - {self._format_changed_file(changed_file)}")
        if diff.filtered_files:
            self._print("Filtered files:")
            for filtered_file in diff.filtered_files:
                self._print(f"  - {self._format_filtered_file(filtered_file)}")
        self._print()

    def render_diff_summary(self, summary: DiffSummary) -> None:
        """Render a concise, escaped summary of the analyzed changes."""

        self._print("Change summary", style=_HEADING_STYLE)
        self._print(f"Purpose: {summary.purpose}")
        if summary.relevant_files:
            self._print("Relevant files:")
            for path in summary.relevant_files:
                self._print(f"  - {self._format_path(path)}")
        if summary.tests:
            self._print("Tests found:")
            for path in summary.tests[:5]:
                self._print(f"  - {self._format_path(path)}")
            if len(summary.tests) > 5:
                self._print(f"  - and {len(summary.tests) - 5} more")
        if summary.side_effects:
            self._print("Likely side effects:")
            for side_effect in summary.side_effects:
                evidence_paths = tuple(
                    dict.fromkeys(citation.file_path for citation in side_effect.evidence)
                )
                evidence = ", ".join(self._format_path(path) for path in evidence_paths[:3])
                self._print(
                    f"  - {side_effect.kind.value}: {side_effect.description} Evidence: {evidence}"
                )
        if summary.uncertainty_notes:
            self._print("Uncertainty:")
            for note in summary.uncertainty_notes[:3]:
                self._print(f"  - {note}")
            if len(summary.uncertainty_notes) > 3:
                self._print(f"  - and {len(summary.uncertainty_notes) - 3} more notes")
        self._print()

    def render_stop_message(self, message: str) -> None:
        """Render an explicit reason that the review cannot ask questions."""

        self._print("Review stopped", style=_WARNING_STYLE)
        self._print(message)

    def render_answer_counts(self, answers: Sequence[Answer]) -> None:
        """Render completion and explicit answered/skipped totals."""

        answered_count = sum(not answer.skipped for answer in answers)
        skipped_count = len(answers) - answered_count
        self._print()
        self._print("Session complete", style=_HEADING_STYLE)
        self._print(f"Answers: {answered_count} answered, {skipped_count} skipped")

    def render_report_written(self, path: Path) -> None:
        """Render the resolved path of a successfully written local report."""

        self._print(f"Report written: {self._format_path(path)}", style=_STATUS_STYLE)

    def capture_answers(self, questions: Sequence[Question]) -> tuple[Answer, ...]:
        """Capture one answer per question until completion or end-of-file."""

        if not questions:
            return ()

        self._print("Questions", style=_HEADING_STYLE)
        self._print("A blank line submits; press Enter immediately to skip.")
        answers: list[Answer] = []
        for index, question in enumerate(questions):
            self._print()
            self._render_question_heading(index, len(questions), question)
            self._print(question.prompt)
            self._print("Answer:")
            lines: list[str] = []
            while True:
                line = self._input.readline()
                if line == "":
                    answer = self._answer(question, lines)
                    answers.append(answer)
                    self._render_answer_status(answer)
                    remaining_count = len(questions) - index - 1
                    answers.extend(self._skipped(remaining) for remaining in questions[index + 1 :])
                    if remaining_count:
                        noun = "question" if remaining_count == 1 else "questions"
                        self._print(
                            f"End of input: {remaining_count} remaining {noun} skipped.",
                            style=_WARNING_STYLE,
                        )
                    return tuple(answers)

                content = self._without_terminal_line_ending(line)
                if content == "":
                    answer = self._answer(question, lines)
                    answers.append(answer)
                    self._render_answer_status(answer)
                    break
                lines.append(content)

        return tuple(answers)

    def capture_follow_up(self, question: Question) -> Answer:
        """Capture one distinctly rendered adaptive follow-up response."""

        self._print()
        self._print("Follow-up", style=_HEADING_STYLE)
        self._print(question.prompt)
        self._print("Answer:")
        lines: list[str] = []
        while True:
            line = self._input.readline()
            content = self._without_terminal_line_ending(line)
            if content == "":
                answer = self._answer(question, lines)
                self._render_answer_status(answer)
                return answer
            lines.append(content)

    def _render_question_heading(
        self,
        index: int,
        question_count: int,
        question: Question,
    ) -> None:
        heading = Text(f"Question {index + 1}/{question_count} — ")
        heading.append(question.category.value.replace("_", " ").title(), _CATEGORY_STYLE)
        self._print(heading)

    def _render_answer_status(self, answer: Answer) -> None:
        if answer.skipped:
            self._print("Skipped.", style=_WARNING_STYLE)
        else:
            self._print("Answer recorded.", style=_STATUS_STYLE)

    def _print(self, content: str | Text = "", *, style: str | None = None) -> None:
        if isinstance(content, Text):
            text = content
        elif style is None:
            text = Text(content)
        else:
            text = Text(content, style=style)
        if self._plain:
            text = Text(text.plain)
        self._console.print(text, soft_wrap=True)
        self._output.flush()

    def _answer(self, question: Question, lines: list[str]) -> Answer:
        raw_text = "\n".join(lines)
        return Answer(
            question_id=question.id,
            raw_text=raw_text,
            timestamp=self._clock(),
            skipped=not lines,
        )

    def _skipped(self, question: Question) -> Answer:
        return Answer(
            question_id=question.id,
            raw_text="",
            timestamp=self._clock(),
            skipped=True,
        )

    @staticmethod
    def _format_changed_file(changed_file: ChangedFile) -> str:
        if changed_file.status is FileStatus.RENAMED and changed_file.old_path is not None:
            path = (
                f"{TerminalSessionAdapter._format_path(changed_file.old_path)} -> "
                f"{TerminalSessionAdapter._format_path(changed_file.path)}"
            )
        else:
            path = TerminalSessionAdapter._format_path(changed_file.path)
        binary_suffix = " (binary)" if changed_file.is_binary else ""
        return f"{changed_file.status.value}: {path}{binary_suffix}"

    @staticmethod
    def _format_filtered_file(filtered_file: FilteredFile) -> str:
        status = filtered_file.status.value if filtered_file.status is not None else "changed"
        path = TerminalSessionAdapter._format_path(filtered_file.path)
        return f"{status}: {path} (filtered: {filtered_file.reason.value})"

    @staticmethod
    def _format_path(path: Path) -> str:
        return json.dumps(str(path), ensure_ascii=True)

    @staticmethod
    def _escape_value(value: str) -> str:
        return json.dumps(value, ensure_ascii=True)[1:-1]

    @staticmethod
    def _without_terminal_line_ending(line: str) -> str:
        if line.endswith("\r\n"):
            return line[:-2]
        if line.endswith("\n"):
            return line[:-1]
        return line
