"""Build validated report payloads from completed review sessions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from coderecall.core.types import (
    Answer,
    Assessment,
    ChangeContext,
    DiffSummary,
    FollowUp,
    Question,
    Report,
)


class ReportBuilder:
    """Validate and assemble one deterministic local report payload."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)

    def build(
        self,
        context: ChangeContext,
        summary: DiffSummary,
        questions: Sequence[Question],
        answers: Sequence[Answer],
        assessments: Sequence[Assessment],
        *,
        follow_up: FollowUp | None = None,
        review_talking_points: Sequence[str] = (),
    ) -> Report:
        """Return a report after validating all question-linked session data."""

        question_ids = tuple(question.id for question in questions)
        answer_ids = tuple(answer.question_id for answer in answers)
        assessment_ids = tuple(assessment.question_id for assessment in assessments)
        self._validate_unique(question_ids, "question IDs must be unique")
        self._validate_unique(answer_ids, "answer question IDs must be unique")
        self._validate_unique(assessment_ids, "assessment question IDs must be unique")
        if set(question_ids) != set(answer_ids) or set(question_ids) != set(assessment_ids):
            raise ValueError("question, answer, and assessment IDs must match")

        self._validate_follow_up(follow_up)
        answers_by_id = {answer.question_id: answer for answer in answers}
        assessments_by_id = {assessment.question_id: assessment for assessment in assessments}
        generated_at = self._clock()
        if generated_at.tzinfo is None or generated_at.utcoffset() is None:
            generated_at = generated_at.replace(tzinfo=UTC)

        return Report(
            session_metadata={
                "branch": context.current_branch,
                "base_branch": context.base_branch,
                "generated_at": generated_at.astimezone(UTC).isoformat(timespec="seconds"),
            },
            diff_summary=summary.purpose,
            questions=tuple(questions),
            answers=tuple(answers_by_id[question_id] for question_id in question_ids),
            assessments=tuple(assessments_by_id[question_id] for question_id in question_ids),
            follow_up=follow_up,
            review_talking_points=tuple(review_talking_points),
        )

    @staticmethod
    def _validate_unique(ids: tuple[str, ...], message: str) -> None:
        if len(ids) != len(set(ids)):
            raise ValueError(message)

    @staticmethod
    def _validate_follow_up(follow_up: FollowUp | None) -> None:
        if follow_up is None:
            return
        question_id = follow_up.question.id
        if follow_up.answer is not None and follow_up.answer.question_id != question_id:
            raise ValueError("follow-up question and answer IDs must match")
        if follow_up.assessment is not None and follow_up.assessment.question_id != question_id:
            raise ValueError("follow-up question and assessment IDs must match")
