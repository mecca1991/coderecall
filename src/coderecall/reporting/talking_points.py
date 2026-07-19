"""Generate concise, deterministic preparation notes for code review."""

from __future__ import annotations

from collections.abc import Sequence

from coderecall.core.types import (
    Assessment,
    AssessmentLabel,
    DiffSummary,
    EvidenceCitation,
    Question,
    QuestionCategory,
)
from coderecall.reporting.formatting import inline_code


class ReviewTalkingPointGenerator:
    """Turn initial question assessments into developer-owned review notes."""

    def generate(
        self,
        summary: DiffSummary,
        questions: Sequence[Question],
        assessments: Sequence[Assessment],
    ) -> tuple[str, ...]:
        """Return one to three stable, single-line review preparation points."""

        question_ids = tuple(question.id for question in questions)
        assessment_ids = tuple(assessment.question_id for assessment in assessments)
        self._validate_unique(question_ids, "question IDs must be unique")
        self._validate_unique(assessment_ids, "assessment question IDs must be unique")
        if set(question_ids) != set(assessment_ids):
            raise ValueError("question and assessment IDs must match")

        assessments_by_id = {assessment.question_id: assessment for assessment in assessments}
        ordered = tuple(
            (index, question, assessments_by_id[question.id])
            for index, question in enumerate(questions)
        )
        points = [f"Explain the change: {self._one_line(summary.purpose)}"]

        preparation = self._preparation_point(summary, ordered)
        if preparation is not None:
            points.append(preparation)

        evidence = self._evidence_point(ordered)
        if evidence is not None:
            points.append(evidence)
        return tuple(points)

    def _preparation_point(
        self,
        summary: DiffSummary,
        ordered: tuple[tuple[int, Question, Assessment], ...],
    ) -> str | None:
        candidates = [
            (
                0 if assessment.label is AssessmentLabel.GAP_FOUND else 1,
                0 if question.category is QuestionCategory.FAILURE else 1,
                index,
                assessment.gaps[0],
            )
            for index, question, assessment in ordered
            if assessment.label in {AssessmentLabel.GAP_FOUND, AssessmentLabel.PARTIAL}
            and assessment.gaps
        ]
        if candidates:
            *_, gap = min(candidates)
            return f"Prepare to discuss: {self._one_line(gap)}"

        effect = next((item for item in summary.side_effects if item.evidence), None)
        if effect is None:
            return None
        citation = self._citation(effect.evidence[0])
        return (
            f"Prepare to discuss this risk: {self._one_line(effect.description)} "
            f"Evidence: {citation}."
        )

    def _evidence_point(
        self,
        ordered: tuple[tuple[int, Question, Assessment], ...],
    ) -> str | None:
        candidates: list[tuple[int, int, Assessment]] = []
        for index, question, assessment in ordered:
            rank = self._assessment_evidence_rank(question, assessment)
            if rank is not None and assessment.matched_evidence:
                candidates.append((rank, index, assessment))
        if not candidates:
            return None

        _, _, selected = min(candidates, key=lambda item: (item[0], item[1]))
        citation = min(
            enumerate(selected.matched_evidence),
            key=lambda item: (self._citation_rank(item[1]), item[0]),
        )[1]
        return f"Evidence to cite: {self._citation(citation)}."

    @staticmethod
    def _assessment_evidence_rank(
        question: Question,
        assessment: Assessment,
    ) -> int | None:
        is_evidence = question.category is QuestionCategory.EVIDENCE
        if assessment.label is AssessmentLabel.STRONG:
            return 0 if is_evidence else 1
        if assessment.label is AssessmentLabel.PARTIAL:
            return 2 if is_evidence else 3
        return None

    @staticmethod
    def _citation_rank(citation: EvidenceCitation) -> int:
        if citation.kind == "test":
            return 0
        if citation.symbol is not None:
            return 1
        if (
            citation.line_start is not None
            or citation.line_end is not None
            or citation.hunk_header is not None
        ):
            return 2
        return 3

    @staticmethod
    def _citation(citation: EvidenceCitation) -> str:
        path = ReviewTalkingPointGenerator._one_line(citation.file_path.as_posix())
        details = [inline_code(path)]
        if citation.symbol is not None:
            symbol = ReviewTalkingPointGenerator._one_line(citation.symbol)
            details.append(f"symbol {inline_code(symbol)}")
        if citation.line_start is not None:
            if citation.line_end is not None and citation.line_end != citation.line_start:
                details.append(f"lines {citation.line_start}-{citation.line_end}")
            else:
                details.append(f"line {citation.line_start}")
        if citation.hunk_header is not None:
            hunk_header = ReviewTalkingPointGenerator._one_line(citation.hunk_header)
            details.append(f"hunk {inline_code(hunk_header)}")
        if citation.note is not None:
            details.append(f"note: {ReviewTalkingPointGenerator._one_line(citation.note)}")
        return "; ".join(details)

    @staticmethod
    def _one_line(value: str) -> str:
        return " ".join(value.split())

    @staticmethod
    def _validate_unique(ids: tuple[str, ...], message: str) -> None:
        if len(ids) != len(set(ids)):
            raise ValueError(message)
