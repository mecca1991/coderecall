"""Render and write deterministic local Markdown reports."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from coderecall.core.errors import ReportWriteFailed
from coderecall.core.types import Assessment, EvidenceCitation, FollowUp, Report


class MarkdownReportWriter:
    """Render report payloads as stable Markdown and write them locally."""

    def render(self, report: Report) -> str:
        """Render a report with stable section ordering and a trailing newline."""

        metadata = report.session_metadata
        lines = [
            "# CodeRecall Report",
            "",
            f"Branch: {metadata.get('branch', '')}",
            f"Base branch: {metadata.get('base_branch', '')}",
            f"Generated: {metadata.get('generated_at', '')}",
            "",
            "## Change Summary",
            "",
            report.diff_summary,
            "",
            "## Questions and Answers",
        ]
        for index, (question, answer, assessment) in enumerate(
            zip(report.questions, report.answers, report.assessments, strict=True),
            start=1,
        ):
            lines.extend(
                [
                    "",
                    f"### {index}. {question.category.value.replace('_', ' ').title()}",
                    "",
                    "**Question**",
                    "",
                    *self._quote(question.prompt),
                    "",
                    "**Answer**",
                    "",
                    *(
                        self._quote("**Skipped.**")
                        if answer.skipped
                        else self._quote(answer.raw_text)
                    ),
                    "",
                    "**Question Citations**",
                    "",
                    *self._citations(question.references, "No question citations."),
                    "",
                    *self._assessment(assessment),
                ]
            )

        if report.follow_up is not None:
            lines.extend(["", *self._follow_up(report.follow_up)])

        lines.extend(["", "## Review Talking Points", ""])
        lines.extend(
            self._items(
                report.review_talking_points,
                "No review talking points generated.",
            )
        )
        return "\n".join(lines) + "\n"

    def write(self, report: Report, path: Path) -> Path:
        """Create parent directories and overwrite ``path`` with UTF-8 Markdown."""

        rendered = self.render(report)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")
        except OSError as error:
            raise ReportWriteFailed(path, error) from error
        return path

    def _follow_up(self, follow_up: FollowUp) -> list[str]:
        answer = follow_up.answer
        lines = [
            "## Follow-Up",
            "",
            "**Question**",
            "",
            *self._quote(follow_up.question.prompt),
            "",
            "**Answer**",
            "",
        ]
        if answer is None or answer.skipped:
            lines.extend(self._quote("**Skipped.**"))
        else:
            lines.extend(self._quote(answer.raw_text))
        lines.extend(
            [
                "",
                "**Citations**",
                "",
                *self._citations(follow_up.question.references, "No follow-up citations."),
            ]
        )
        if follow_up.assessment is not None:
            lines.extend(["", *self._assessment(follow_up.assessment)])
        return lines

    def _assessment(self, assessment: Assessment) -> list[str]:
        return [
            f"**Assessment:** {assessment.label.value}",
            "",
            f"**Confidence:** {assessment.confidence}",
            "",
            "**Summary**",
            "",
            *self._quote(assessment.summary),
            "",
            "**Strengths**",
            "",
            *self._items(assessment.strengths, "No strengths identified."),
            "",
            "**Gaps**",
            "",
            *self._items(assessment.gaps, "No gaps identified."),
            "",
            "**Uncertainty Notes**",
            "",
            *self._items(assessment.uncertainty_notes, "No uncertainty notes."),
            "",
            "**Evidence**",
            "",
            *self._citations(assessment.evidence, "No assessment evidence."),
        ]

    def _citations(
        self,
        citations: Sequence[EvidenceCitation],
        empty_message: str,
    ) -> list[str]:
        if not citations:
            return [f"- {empty_message}"]
        return [f"- {self._citation(citation)}" for citation in citations]

    def _citation(self, citation: EvidenceCitation) -> str:
        details = [self._inline_code(citation.file_path.as_posix())]
        if citation.symbol is not None:
            details.append(f"symbol {self._inline_code(citation.symbol)}")
        if citation.line_start is not None:
            if citation.line_end is not None and citation.line_end != citation.line_start:
                details.append(f"lines {citation.line_start}-{citation.line_end}")
            else:
                details.append(f"line {citation.line_start}")
        if citation.hunk_header is not None:
            details.append(f"hunk {self._inline_code(citation.hunk_header)}")
        if citation.note is not None:
            details.append(f"note: {self._one_line(citation.note)}")
        return "; ".join(details)

    @staticmethod
    def _quote(value: str) -> list[str]:
        return [">" if line == "" else f"> {line}" for line in value.split("\n")]

    @staticmethod
    def _items(values: Sequence[str], empty_message: str) -> list[str]:
        if not values:
            return [f"- {empty_message}"]
        return [f"- {MarkdownReportWriter._one_line(value)}" for value in values]

    @staticmethod
    def _one_line(value: str) -> str:
        return " ".join(value.splitlines())

    @staticmethod
    def _inline_code(value: str) -> str:
        longest_run = max((len(run) for run in re.findall(r"`+", value)), default=0)
        delimiter = "`" * max(1, longest_run + 1)
        padding = " " if value.startswith("`") or value.endswith("`") else ""
        return f"{delimiter}{padding}{value}{padding}{delimiter}"
