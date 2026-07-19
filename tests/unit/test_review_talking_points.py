"""Tests for deterministic, developer-owned review talking points."""

from __future__ import annotations

from pathlib import Path

import pytest

from coderecall.core.types import (
    Assessment,
    AssessmentLabel,
    DiffSummary,
    EvidenceCitation,
    LikelySideEffect,
    Question,
    QuestionCategory,
    SideEffectKind,
)
from coderecall.reporting import ReviewTalkingPointGenerator


def question(question_id: str, category: QuestionCategory) -> Question:
    return Question(
        id=question_id,
        category=category,
        prompt=f"Question about {question_id}?",
        rationale="The changed area is relevant.",
    )


def assessment(
    question_id: str,
    label: AssessmentLabel,
    *,
    gaps: tuple[str, ...] = (),
    matched_evidence: tuple[EvidenceCitation, ...] = (),
) -> Assessment:
    return Assessment(
        question_id=question_id,
        label=label,
        summary="Preparation feedback.",
        confidence="medium",
        gaps=gaps,
        matched_evidence=matched_evidence,
    )


def test_generate_returns_summary_gap_and_evidence_in_stable_order() -> None:
    test_citation = EvidenceCitation("test", Path("tests/payment_handler.test.ts"))
    questions = (
        question("behavior", QuestionCategory.BEHAVIOR),
        question("failure", QuestionCategory.FAILURE),
        question("evidence", QuestionCategory.EVIDENCE),
    )
    assessments = (
        assessment("behavior", AssessmentLabel.STRONG),
        assessment(
            "failure",
            AssessmentLabel.GAP_FOUND,
            gaps=("Revisit the rollback claim before review.",),
        ),
        assessment(
            "evidence",
            AssessmentLabel.STRONG,
            matched_evidence=(test_citation,),
        ),
    )

    points = ReviewTalkingPointGenerator().generate(
        DiffSummary(purpose="Likely adds payment idempotency."),
        questions,
        assessments,
    )

    assert points == (
        "Explain the change: Likely adds payment idempotency.",
        "Prepare to discuss: Revisit the rollback claim before review.",
        "Evidence to cite: `tests/payment_handler.test.ts`.",
    )


def test_generate_returns_only_the_summary_when_no_preparation_or_evidence_point_exists() -> None:
    points = ReviewTalkingPointGenerator().generate(
        DiffSummary(purpose="Updates local settings."),
        (question("behavior", QuestionCategory.BEHAVIOR),),
        (assessment("behavior", AssessmentLabel.UNCERTAIN),),
    )

    assert points == ("Explain the change: Updates local settings.",)


def test_gap_priority_is_label_then_failure_category_then_question_order() -> None:
    questions = (
        question("partial-failure", QuestionCategory.FAILURE),
        question("gap-behavior", QuestionCategory.BEHAVIOR),
        question("gap-failure-first", QuestionCategory.FAILURE),
        question("gap-failure-later", QuestionCategory.FAILURE),
    )
    assessments = (
        assessment(
            "gap-failure-first",
            AssessmentLabel.GAP_FOUND,
            gaps=("Use the earliest failure-category Gap found assessment.",),
        ),
        assessment(
            "partial-failure",
            AssessmentLabel.PARTIAL,
            gaps=("A Partial failure gap ranks after every Gap found assessment.",),
        ),
        assessment(
            "gap-failure-later",
            AssessmentLabel.GAP_FOUND,
            gaps=("This later failure question should not win.",),
        ),
        assessment(
            "gap-behavior",
            AssessmentLabel.GAP_FOUND,
            gaps=("A behavior gap ranks after failure gaps.",),
        ),
    )

    points = ReviewTalkingPointGenerator().generate(
        DiffSummary(purpose="Changes a transaction flow."),
        questions,
        assessments,
    )

    assert points[1] == (
        "Prepare to discuss: Use the earliest failure-category Gap found assessment."
    )


def test_first_repository_backed_side_effect_is_used_when_no_gap_exists() -> None:
    unbacked = LikelySideEffect(
        SideEffectKind.FILE_WRITE,
        "May write a local file.",
        (),
    )
    backed_citation = EvidenceCitation(
        "call",
        Path("src/payment_service.ts"),
        symbol="processor.charge",
        line_start=7,
    )
    backed = LikelySideEffect(
        SideEffectKind.NETWORK_CALL,
        "The changed flow may leave an external charge after local rollback.",
        (backed_citation,),
    )

    points = ReviewTalkingPointGenerator().generate(
        DiffSummary(
            purpose="Changes payment capture.",
            side_effects=(unbacked, backed),
        ),
        (question("behavior", QuestionCategory.BEHAVIOR),),
        (assessment("behavior", AssessmentLabel.STRONG),),
    )

    assert points == (
        "Explain the change: Changes payment capture.",
        "Prepare to discuss this risk: The changed flow may leave an external charge after "
        "local rollback. Evidence: `src/payment_service.ts`; symbol `processor.charge`; line 7.",
    )


def test_evidence_answer_ranking_precedes_citation_specificity() -> None:
    partial_test = EvidenceCitation("test", Path("tests/retry_test.py"))
    strong_file = EvidenceCitation("file", Path("src/retry.py"))
    strong_evidence_symbol = EvidenceCitation(
        "symbol",
        Path("src/retry.py"),
        symbol="retry_payment",
    )
    questions = (
        question("partial-evidence", QuestionCategory.EVIDENCE),
        question("strong-behavior", QuestionCategory.BEHAVIOR),
        question("strong-evidence", QuestionCategory.EVIDENCE),
    )
    assessments = (
        assessment(
            "partial-evidence",
            AssessmentLabel.PARTIAL,
            matched_evidence=(partial_test,),
        ),
        assessment(
            "strong-behavior",
            AssessmentLabel.STRONG,
            matched_evidence=(strong_file,),
        ),
        assessment(
            "strong-evidence",
            AssessmentLabel.STRONG,
            matched_evidence=(strong_evidence_symbol,),
        ),
    )

    points = ReviewTalkingPointGenerator().generate(
        DiffSummary(purpose="Changes retry handling."),
        questions,
        assessments,
    )

    assert points[-1] == "Evidence to cite: `src/retry.py`; symbol `retry_payment`."


def test_evidence_specificity_prefers_test_symbol_line_or_hunk_then_file() -> None:
    citations = (
        EvidenceCitation("file", Path("src/payment.py")),
        EvidenceCitation("call", Path("src/payment.py"), line_start=18),
        EvidenceCitation("symbol", Path("src/payment.py"), symbol="capture"),
        EvidenceCitation("test", Path("tests/payment_test.py")),
    )
    questions = (question("evidence", QuestionCategory.EVIDENCE),)

    points = ReviewTalkingPointGenerator().generate(
        DiffSummary(purpose="Changes capture behavior."),
        questions,
        (
            assessment(
                "evidence",
                AssessmentLabel.STRONG,
                matched_evidence=citations,
            ),
        ),
    )

    assert points[-1] == "Evidence to cite: `tests/payment_test.py`."


def test_generate_normalizes_whitespace_and_safely_formats_inline_code() -> None:
    citation = EvidenceCitation(
        "call",
        Path("`src/payment.py"),
        symbol="charge`",
        hunk_header="`",
    )
    points = ReviewTalkingPointGenerator().generate(
        DiffSummary(purpose="Likely\n  updates   payment capture."),
        (question("behavior", QuestionCategory.BEHAVIOR),),
        (
            assessment(
                "behavior",
                AssessmentLabel.STRONG,
                matched_evidence=(citation,),
            ),
        ),
    )

    assert points == (
        "Explain the change: Likely updates payment capture.",
        "Evidence to cite: `` `src/payment.py ``; symbol `` charge` ``; hunk `` ` ``.",
    )
    assert all("\n" not in point for point in points)


@pytest.mark.parametrize(
    ("questions", "assessments", "message"),
    (
        (
            (
                question("behavior", QuestionCategory.BEHAVIOR),
                question("behavior", QuestionCategory.FAILURE),
            ),
            (assessment("behavior", AssessmentLabel.STRONG),),
            "question IDs must be unique",
        ),
        (
            (question("behavior", QuestionCategory.BEHAVIOR),),
            (
                assessment("behavior", AssessmentLabel.STRONG),
                assessment("behavior", AssessmentLabel.PARTIAL),
            ),
            "assessment question IDs must be unique",
        ),
        (
            (question("behavior", QuestionCategory.BEHAVIOR),),
            (assessment("failure", AssessmentLabel.STRONG),),
            "question and assessment IDs must match",
        ),
    ),
)
def test_generate_rejects_invalid_question_and_assessment_ids(
    questions: tuple[Question, ...],
    assessments: tuple[Assessment, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ReviewTalkingPointGenerator().generate(
            DiffSummary(purpose="Summary."),
            questions,
            assessments,
        )


def test_uncertain_and_gap_found_matched_evidence_cannot_create_evidence_point() -> None:
    citation = EvidenceCitation("test", Path("tests/payment_test.py"))
    questions = (
        question("failure", QuestionCategory.FAILURE),
        question("evidence", QuestionCategory.EVIDENCE),
    )
    assessments = (
        assessment(
            "failure",
            AssessmentLabel.GAP_FOUND,
            gaps=("Prepare the failure explanation.",),
            matched_evidence=(citation,),
        ),
        assessment(
            "evidence",
            AssessmentLabel.UNCERTAIN,
            matched_evidence=(citation,),
        ),
    )

    points = ReviewTalkingPointGenerator().generate(
        DiffSummary(purpose="Changes payment capture."),
        questions,
        assessments,
    )

    assert points == (
        "Explain the change: Changes payment capture.",
        "Prepare to discuss: Prepare the failure explanation.",
    )


def test_assessment_input_order_does_not_change_question_order_tiebreaks() -> None:
    questions = (
        question("first", QuestionCategory.BEHAVIOR),
        question("second", QuestionCategory.BEHAVIOR),
    )
    assessments = (
        assessment("second", AssessmentLabel.PARTIAL, gaps=("Second gap.",)),
        assessment("first", AssessmentLabel.PARTIAL, gaps=("First gap.",)),
    )

    points = ReviewTalkingPointGenerator().generate(
        DiffSummary(purpose="Changes behavior."),
        questions,
        assessments,
    )

    assert points[1] == "Prepare to discuss: First gap."
