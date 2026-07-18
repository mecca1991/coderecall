"""Plain-text terminal adapter for capturing review answers."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TextIO

from coderecall.core.types import Answer, Question


class TerminalSessionAdapter:
    """Render questions and return ordered, structured terminal answers."""

    def __init__(
        self,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._input = input_stream if input_stream is not None else sys.stdin
        self._output = output_stream if output_stream is not None else sys.stdout
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)

    def capture_answers(self, questions: Sequence[Question]) -> tuple[Answer, ...]:
        """Capture one answer per question until completion or end-of-file."""

        if not questions:
            return ()

        self._write("A blank line submits; press Enter immediately to skip.\n")
        answers: list[Answer] = []
        for index, question in enumerate(questions):
            self._write(
                f"\nQuestion {index + 1} of {len(questions)} [{question.category.value}]\n"
                f"{question.prompt}\n"
                "Answer:\n"
            )
            lines: list[str] = []
            while True:
                line = self._input.readline()
                if line == "":
                    answers.append(self._answer(question, lines))
                    answers.extend(self._skipped(remaining) for remaining in questions[index + 1 :])
                    return tuple(answers)

                content = self._without_terminal_line_ending(line)
                if content == "":
                    answers.append(self._answer(question, lines))
                    break
                lines.append(content)

        return tuple(answers)

    def _write(self, text: str) -> None:
        self._output.write(text)
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
    def _without_terminal_line_ending(line: str) -> str:
        if line.endswith("\r\n"):
            return line[:-2]
        if line.endswith("\n"):
            return line[:-1]
        return line
