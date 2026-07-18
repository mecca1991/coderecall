"""Tests for deterministic, evidence-grounded adaptive follow-up selection."""

from __future__ import annotations

from pathlib import Path

import pytest

from coderecall.core.types import (
    Assessment,
    AssessmentLabel,
    ChangeContext,
    ChangedFile,
    EvidenceCitation,
    FileStatus,
    Question,
    QuestionCategory,
)
from coderecall.evaluation import FollowUpSelector

SOURCE_PATH = Path("src/payment_service.ts")
TEST_PATH = Path("tests/payment_service.test.ts")
OUTSIDE_PATH = Path("vendor/processor.ts")
CHARGE = EvidenceCitation("call", SOURCE_PATH, "processor.charge", line_start=7)
TRANSACTION = EvidenceCitation("call", SOURCE_PATH, "database.transaction", line_start=6)
TEST = EvidenceCitation("test", TEST_PATH)
OUTSIDE = EvidenceCitation("call", OUTSIDE_PATH, "processor.refund", line_start=20)


def context() -> ChangeContext:
    return ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/payment-idempotency",
        base_branch="main",
        changed_files=(
            ChangedFile(SOURCE_PATH, FileStatus.MODIFIED),
            ChangedFile(TEST_PATH, FileStatus.MODIFIED, is_test=True),
        ),
    )


def question(
    question_id: str,
    category: QuestionCategory,
    *references: EvidenceCitation,
) -> Question:
    return Question(
        id=question_id,
        category=category,
        prompt=f"Prompt for {question_id}?",
        rationale="The changed branch provides local evidence.",
        references=references,
    )


def assessment(
    question_id: str,
    label: AssessmentLabel,
    *evidence: EvidenceCitation,
    gaps: tuple[str, ...] = ("Explain the remaining repository-backed risk.",),
) -> Assessment:
    return Assessment(
        question_id=question_id,
        label=label,
        summary="Grounded preparation feedback.",
        confidence="medium",
        gaps=gaps,
        evidence=evidence,
    )


def test_selects_a_grounded_gap_and_builds_a_stable_respectful_question() -> None:
    questions = (question("failure", QuestionCategory.FAILURE, CHARGE, TRANSACTION),)
    gap = (
        "Revisit the rollback claim: the local transaction does not reverse processor.charge, "
        "so reconciliation is still needed."
    )
    assessments = (
        assessment(
            "failure",
            AssessmentLabel.GAP_FOUND,
            CHARGE,
            TRANSACTION,
            gaps=(gap,),
        ),
    )

    follow_up = FollowUpSelector().select(context(), questions, assessments)

    assert follow_up is not None
    assert follow_up.question.id == "failure-follow-up"
    assert follow_up.question.category is QuestionCategory.FOLLOW_UP
    assert gap in follow_up.question.prompt
    assert "processor.charge" in follow_up.question.prompt
    assert "database.transaction" in follow_up.question.prompt
    assert "reconciliation" in follow_up.question.prompt
    assert "incorrect" not in follow_up.question.prompt.lower()
    assert "failed" not in follow_up.question.prompt.lower()
    assert follow_up.question.references == (CHARGE, TRANSACTION)
    assert follow_up.answer is None
    assert follow_up.assessment is None


def test_partial_assessment_can_produce_one_follow_up() -> None:
    questions = (question("behavior", QuestionCategory.BEHAVIOR, CHARGE),)
    assessments = (
        assessment("behavior", AssessmentLabel.PARTIAL, CHARGE, gaps=("Name the retry path.",)),
    )

    follow_up = FollowUpSelector().select(context(), questions, assessments)

    assert follow_up is not None
    assert follow_up.question.id == "behavior-follow-up"
    assert "Name the retry path." in follow_up.question.prompt


def test_priority_is_label_then_category_then_original_question_order() -> None:
    questions = (
        question("evidence-first", QuestionCategory.EVIDENCE, TEST),
        question("behavior-gap", QuestionCategory.BEHAVIOR, CHARGE),
        question("failure-partial", QuestionCategory.FAILURE, TRANSACTION),
        question("failure-gap", QuestionCategory.FAILURE, CHARGE, TRANSACTION),
        question("failure-gap-later", QuestionCategory.FAILURE, CHARGE),
    )
    assessments = (
        assessment("evidence-first", AssessmentLabel.GAP_FOUND, TEST),
        assessment("behavior-gap", AssessmentLabel.GAP_FOUND, CHARGE),
        assessment("failure-partial", AssessmentLabel.PARTIAL, TRANSACTION),
        assessment("failure-gap", AssessmentLabel.GAP_FOUND, CHARGE, TRANSACTION),
        assessment("failure-gap-later", AssessmentLabel.GAP_FOUND, CHARGE),
    )

    follow_up = FollowUpSelector().select(context(), questions, assessments)

    assert follow_up is not None
    assert follow_up.question.id == "failure-gap-follow-up"


def test_citations_are_filtered_and_deduplicated_in_stable_order() -> None:
    questions = (question("failure", QuestionCategory.FAILURE),)
    assessments = (
        assessment(
            "failure",
            AssessmentLabel.GAP_FOUND,
            TRANSACTION,
            OUTSIDE,
            CHARGE,
            TRANSACTION,
        ),
    )

    follow_up = FollowUpSelector().select(context(), questions, assessments)

    assert follow_up is not None
    assert follow_up.question.references == (TRANSACTION, CHARGE)


@pytest.mark.parametrize("label", (AssessmentLabel.STRONG, AssessmentLabel.UNCERTAIN))
def test_non_gap_labels_do_not_produce_a_follow_up(label: AssessmentLabel) -> None:
    result = FollowUpSelector().select(
        context(),
        (question("failure", QuestionCategory.FAILURE, CHARGE),),
        (assessment("failure", label, CHARGE),),
    )

    assert result is None


@pytest.mark.parametrize(
    "candidate",
    (
        assessment("failure", AssessmentLabel.GAP_FOUND, gaps=("A specific gap.",)),
        assessment("failure", AssessmentLabel.GAP_FOUND, OUTSIDE),
        assessment("failure", AssessmentLabel.GAP_FOUND, CHARGE, gaps=()),
        assessment("failure", AssessmentLabel.GAP_FOUND, CHARGE, gaps=("", "   ")),
    ),
)
def test_ungrounded_or_gap_free_assessments_do_not_produce_a_follow_up(
    candidate: Assessment,
) -> None:
    result = FollowUpSelector().select(
        context(),
        (question("failure", QuestionCategory.FAILURE, CHARGE),),
        (candidate,),
    )

    assert result is None


def test_disabled_selection_returns_none_for_an_otherwise_grounded_gap() -> None:
    result = FollowUpSelector().select(
        context(),
        (question("failure", QuestionCategory.FAILURE, CHARGE),),
        (assessment("failure", AssessmentLabel.GAP_FOUND, CHARGE),),
        enabled=False,
    )

    assert result is None


@pytest.mark.parametrize(
    ("questions", "assessments", "message"),
    (
        (
            (
                question("failure", QuestionCategory.FAILURE, CHARGE),
                question("failure", QuestionCategory.BEHAVIOR, CHARGE),
            ),
            (assessment("failure", AssessmentLabel.PARTIAL, CHARGE),),
            "question IDs must be unique",
        ),
        (
            (question("failure", QuestionCategory.FAILURE, CHARGE),),
            (
                assessment("failure", AssessmentLabel.PARTIAL, CHARGE),
                assessment("failure", AssessmentLabel.GAP_FOUND, CHARGE),
            ),
            "assessment IDs must be unique",
        ),
        (
            (question("behavior", QuestionCategory.BEHAVIOR, CHARGE),),
            (assessment("failure", AssessmentLabel.PARTIAL, CHARGE),),
            "question and assessment IDs must match",
        ),
    ),
)
def test_question_and_assessment_id_invariants_are_enforced(
    questions: tuple[Question, ...],
    assessments: tuple[Assessment, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        FollowUpSelector().select(context(), questions, assessments, enabled=False)
