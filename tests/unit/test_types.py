"""Tests for core CodeRecall data types."""

from __future__ import annotations

from pathlib import Path

from coderecall.core.types import (
    Answer,
    Assessment,
    AssessmentLabel,
    ChangeContext,
    ChangedFile,
    EvidenceCitation,
    FileStatus,
    LikelySideEffect,
    Question,
    QuestionCategory,
    Report,
    SideEffectKind,
)


def test_change_context_captures_changed_and_filtered_files() -> None:
    changed_file = ChangedFile(path=Path("app/payments.py"), status=FileStatus.MODIFIED)
    citation = EvidenceCitation(kind="call", file_path=changed_file.path, symbol="processor.charge")
    side_effect = LikelySideEffect(
        kind=SideEffectKind.NETWORK_CALL,
        description="The change likely makes an external network call.",
        evidence=(citation,),
    )

    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/payment-idempotency",
        base_branch="main",
        changed_files=(changed_file,),
        likely_side_effects=(side_effect,),
        uncertainty_notes=("Could not trace every downstream side effect.",),
    )

    assert context.current_branch == "feature/payment-idempotency"
    assert context.base_branch == "main"
    assert context.changed_files == (changed_file,)
    assert context.likely_side_effects == (side_effect,)
    assert context.uncertainty_notes


def test_question_answer_assessment_and_report_payload() -> None:
    citation = EvidenceCitation(
        kind="file",
        file_path=Path("app/payments.py"),
        symbol="create_payment",
        note="Payment creation flow changed.",
    )
    question = Question(
        id="q1",
        category=QuestionCategory.BEHAVIOR,
        prompt="What behavior does this branch introduce?",
        rationale="The payment flow changed.",
        references=(citation,),
    )
    answer = Answer(question_id="q1", raw_text="It adds idempotency handling.")
    assessment = Assessment(
        question_id="q1",
        label=AssessmentLabel.PARTIAL,
        summary="The answer identifies idempotency but misses retry behavior.",
        confidence="medium",
        gaps=("Retry behavior is not described.",),
        evidence=(citation,),
    )

    report = Report(
        session_metadata={"branch": "feature/payment-idempotency", "base": "main"},
        diff_summary="Payment idempotency changed.",
        questions=(question,),
        answers=(answer,),
        assessments=(assessment,),
        review_talking_points=("Explain retry behavior.",),
    )

    assert report.questions[0].category is QuestionCategory.BEHAVIOR
    assert report.answers[0].skipped is False
    assert report.assessments[0].label is AssessmentLabel.PARTIAL
    assert report.review_talking_points == ("Explain retry behavior.",)
