"""Deterministic selection of one evidence-grounded adaptive follow-up."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from coderecall.core.types import (
    Assessment,
    AssessmentLabel,
    ChangeContext,
    EvidenceCitation,
    FollowUp,
    Question,
    QuestionCategory,
)

_LABEL_PRIORITY = {
    AssessmentLabel.GAP_FOUND: 0,
    AssessmentLabel.PARTIAL: 1,
}


class FollowUpSelector:
    """Select and construct at most one grounded follow-up question."""

    def select(
        self,
        context: ChangeContext,
        questions: Sequence[Question],
        assessments: Sequence[Assessment],
        *,
        enabled: bool = True,
    ) -> FollowUp | None:
        """Return the highest-priority grounded follow-up, if one exists."""

        question_ids = tuple(question.id for question in questions)
        assessment_ids = tuple(assessment.question_id for assessment in assessments)
        self._validate_ids(question_ids, assessment_ids)
        if not enabled:
            return None

        changed_paths = {changed_file.path for changed_file in context.changed_files}
        assessment_by_id = {assessment.question_id: assessment for assessment in assessments}
        candidates: list[
            tuple[int, int, int, Question, str, tuple[EvidenceCitation, ...]]
        ] = []
        for index, question in enumerate(questions):
            assessment = assessment_by_id[question.id]
            if assessment.label not in _LABEL_PRIORITY:
                continue
            gap = next((gap.strip() for gap in assessment.gaps if gap.strip()), None)
            evidence = self._sanitize(assessment.evidence, changed_paths)
            if gap is None or not evidence:
                continue
            category_priority = 0 if question.category is QuestionCategory.FAILURE else 1
            candidates.append(
                (
                    _LABEL_PRIORITY[assessment.label],
                    category_priority,
                    index,
                    question,
                    gap,
                    evidence,
                )
            )

        if not candidates:
            return None

        _, _, _, source, gap, evidence = min(candidates, key=lambda item: item[:3])
        follow_up_question = Question(
            id=f"{source.id}-follow-up",
            category=QuestionCategory.FOLLOW_UP,
            prompt=(
                f"To prepare for review, consider this gap: {gap} "
                "How would you explain it using the cited changed-file evidence?"
            ),
            rationale=(
                "This follow-up focuses on one specific gap supported by local changed-file "
                "evidence."
            ),
            references=evidence,
        )
        return FollowUp(question=follow_up_question)

    @staticmethod
    def _validate_ids(question_ids: tuple[str, ...], assessment_ids: tuple[str, ...]) -> None:
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("question IDs must be unique")
        if len(set(assessment_ids)) != len(assessment_ids):
            raise ValueError("assessment IDs must be unique")
        if set(question_ids) != set(assessment_ids):
            raise ValueError("question and assessment IDs must match")

    @staticmethod
    def _sanitize(
        citations: tuple[EvidenceCitation, ...],
        changed_paths: set[Path],
    ) -> tuple[EvidenceCitation, ...]:
        return tuple(dict.fromkeys(item for item in citations if item.file_path in changed_paths))
