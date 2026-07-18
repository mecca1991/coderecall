"""Tests for deterministic answer evaluation against repository evidence."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from coderecall.core.types import (
    Answer,
    AssessmentLabel,
    ChangeContext,
    ChangedFile,
    ChangedSymbol,
    CodeReference,
    EvidenceCitation,
    FileStatus,
    LikelySideEffect,
    Question,
    QuestionCategory,
    SideEffectKind,
)
from coderecall.evaluation import Evaluator, HeuristicEvaluator
from coderecall.evaluation.heuristic_evaluator import _Concept

SERVICE_PATH = Path("src/payment_service.ts")
HANDLER_PATH = Path("src/payment_handler.ts")
TEST_PATH = Path("tests/payment_handler.test.ts")
NETWORK_CITATION = EvidenceCitation(
    kind="call",
    file_path=SERVICE_PATH,
    symbol="processor.charge",
    hunk_header="@@ -4,3 +4,8 @@ capturePayment",
    line_start=7,
    line_end=7,
    note="Added call in the changed hunk.",
)
TRANSACTION_CITATION = EvidenceCitation(
    kind="call",
    file_path=SERVICE_PATH,
    symbol="database.transaction",
    hunk_header="@@ -4,3 +4,8 @@ capturePayment",
    line_start=6,
    line_end=6,
)
HANDLER_CITATION = EvidenceCitation(
    kind="symbol",
    file_path=HANDLER_PATH,
    symbol="handlePayment",
    line_start=4,
)
TEST_CITATION = EvidenceCitation(kind="test", file_path=TEST_PATH)


def payment_context() -> ChangeContext:
    return ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/payment-idempotency",
        base_branch="main",
        changed_files=(
            ChangedFile(path=HANDLER_PATH, status=FileStatus.MODIFIED),
            ChangedFile(path=SERVICE_PATH, status=FileStatus.MODIFIED),
            ChangedFile(path=TEST_PATH, status=FileStatus.MODIFIED, is_test=True),
        ),
        changed_symbols=(
            ChangedSymbol(HANDLER_PATH, "handlePayment", "function", 4),
            ChangedSymbol(SERVICE_PATH, "capturePayment", "function", 4),
        ),
        call_sites=(
            CodeReference(HANDLER_PATH, "call", "payments.findByIdempotencyKey", 5),
            CodeReference(HANDLER_PATH, "call", "capturePayment", 9),
            CodeReference(SERVICE_PATH, "call", "database.transaction", 5),
            CodeReference(SERVICE_PATH, "call", "processor.charge", 6),
        ),
        related_tests=(TEST_PATH,),
        likely_side_effects=(
            LikelySideEffect(
                SideEffectKind.NETWORK_CALL,
                "The change likely makes an external network call.",
                (NETWORK_CITATION,),
            ),
            LikelySideEffect(
                SideEffectKind.TRANSACTION_BOUNDARY,
                "The change likely creates a local transaction boundary.",
                (TRANSACTION_CITATION,),
            ),
        ),
    )


def failure_question(*references: EvidenceCitation) -> Question:
    return Question(
        id="failure",
        category=QuestionCategory.FAILURE,
        prompt="What can remain after partial success, and how should retry or recovery work?",
        rationale="The changed flow crosses likely external and transaction boundaries.",
        references=references or (NETWORK_CITATION, TRANSACTION_CITATION),
    )


def test_evaluator_protocol_is_satisfied() -> None:
    evaluator: Evaluator = HeuristicEvaluator()

    assert evaluator is not None


def test_side_effect_concept_label_uses_the_enum_string_value() -> None:
    citation = EvidenceCitation(kind="call", file_path=SERVICE_PATH)
    concept = _Concept(citation, ("network call",), SideEffectKind.NETWORK_CALL)

    assert concept.label == SideEffectKind.NETWORK_CALL.value
    assert type(concept.label) is str


@pytest.mark.parametrize(
    ("raw_text", "expected_label"),
    (
        ("The charge may need retry recovery.", AssessmentLabel.STRONG),
        ("The recharged request may need retry recovery.", AssessmentLabel.UNCERTAIN),
    ),
)
def test_concept_matching_preserves_whole_phrase_boundaries(
    raw_text: str,
    expected_label: AssessmentLabel,
) -> None:
    citation = EvidenceCitation(kind="call", file_path=SERVICE_PATH, symbol="charge")
    context = replace(
        payment_context(),
        likely_side_effects=(
            LikelySideEffect(
                SideEffectKind.NETWORK_CALL,
                "The change likely makes an external network call.",
                (citation,),
            ),
        ),
    )

    assessment = HeuristicEvaluator().evaluate(
        context,
        failure_question(citation),
        Answer(question_id="failure", raw_text=raw_text),
    )

    assert assessment.label is expected_label


def test_strong_failure_answer_connects_every_boundary_to_recovery() -> None:
    assessment = HeuristicEvaluator().evaluate(
        payment_context(),
        failure_question(),
        Answer(
            question_id="failure",
            raw_text=(
                "processor.charge can succeed before database.transaction rolls back. "
                "A retry could charge again, so the flow needs idempotency or reconciliation."
            ),
        ),
    )

    assert assessment.label is AssessmentLabel.STRONG
    assert assessment.confidence == "medium"
    assert assessment.evidence == (NETWORK_CITATION, TRANSACTION_CITATION)
    assert any("processor.charge" in strength for strength in assessment.strengths)
    assert any("database.transaction" in strength for strength in assessment.strengths)
    assert assessment.gaps == ()
    assert "review" in assessment.summary.lower()


def test_partial_answer_keeps_repository_evidence_and_names_missing_reasoning() -> None:
    question = Question(
        id="behavior",
        category=QuestionCategory.BEHAVIOR,
        prompt="What behavior does handlePayment modify?",
        rationale="The handler changed.",
        references=(HANDLER_CITATION,),
    )

    assessment = HeuristicEvaluator().evaluate(
        payment_context(),
        question,
        Answer(question_id="behavior", raw_text="handlePayment changes the payment flow."),
    )

    assert assessment.label is AssessmentLabel.PARTIAL
    assert assessment.confidence == "medium"
    assert assessment.evidence == (HANDLER_CITATION,)
    assert assessment.strengths
    assert any("concrete" in gap.lower() for gap in assessment.gaps)


def test_generic_path_token_cannot_satisfy_failure_evidence_groups() -> None:
    assessment = HeuristicEvaluator().evaluate(
        payment_context(),
        failure_question(),
        Answer(question_id="failure", raw_text="src retry"),
    )

    assert assessment.label is AssessmentLabel.UNCERTAIN


def test_secondary_symbol_cannot_stand_in_for_the_primary_behavior_area() -> None:
    question = Question(
        id="behavior",
        category=QuestionCategory.BEHAVIOR,
        prompt="What behavior does handlePayment modify?",
        rationale="The handler changed.",
        references=(HANDLER_CITATION,),
    )

    assessment = HeuristicEvaluator().evaluate(
        payment_context(),
        question,
        Answer(question_id="behavior", raw_text="capturePayment changes the flow."),
    )

    assert assessment.label is AssessmentLabel.PARTIAL
    assert any("handlePayment" in gap for gap in assessment.gaps)


def test_every_repository_concept_used_as_a_strength_is_cited() -> None:
    question = Question(
        id="behavior",
        category=QuestionCategory.BEHAVIOR,
        prompt="What behavior does handlePayment modify?",
        rationale="The handler changed.",
        references=(HANDLER_CITATION,),
    )

    assessment = HeuristicEvaluator().evaluate(
        payment_context(),
        question,
        Answer(question_id="behavior", raw_text="processor.charge changes the external flow."),
    )

    assert any("processor.charge" in strength for strength in assessment.strengths)
    assert any(citation.symbol == "processor.charge" for citation in assessment.evidence)


def test_explicit_local_rollback_misconception_is_a_gap() -> None:
    assessment = HeuristicEvaluator().evaluate(
        payment_context(),
        failure_question(),
        Answer(
            question_id="failure",
            raw_text=("database.transaction rollback undoes processor.charge, so retry is safe."),
        ),
    )

    assert assessment.label is AssessmentLabel.GAP_FOUND
    assert assessment.confidence == "medium"
    assert assessment.evidence == (NETWORK_CITATION, TRANSACTION_CITATION)
    assert any("rollback" in gap.lower() and "external" in gap.lower() for gap in assessment.gaps)


def test_unrelated_negation_does_not_hide_rollback_misconception() -> None:
    assessment = HeuristicEvaluator().evaluate(
        payment_context(),
        failure_question(),
        Answer(
            question_id="failure",
            raw_text=(
                "database.transaction rollback undoes processor.charge, so retry is not needed."
            ),
        ),
    )

    assert assessment.label is AssessmentLabel.GAP_FOUND


@pytest.mark.parametrize(
    "rollback_explanation",
    (
        "database.transaction rollback does not undo processor.charge.",
        "database.transaction rollback doesn't undo processor.charge.",
        "processor.charge remains after database.transaction rollback.",
        "database.transaction rollback undoes database writes while processor.charge remains.",
    ),
)
def test_negated_or_non_conflicting_rollback_statement_is_not_a_gap(
    rollback_explanation: str,
) -> None:
    assessment = HeuristicEvaluator().evaluate(
        payment_context(),
        failure_question(),
        Answer(
            question_id="failure",
            raw_text=(
                f"{rollback_explanation} A retry could charge again, so recovery needs "
                "reconciliation."
            ),
        ),
    )

    assert assessment.label is AssessmentLabel.STRONG
    assert assessment.label is not AssessmentLabel.GAP_FOUND


@pytest.mark.parametrize(
    ("context", "question", "answer", "expected_note"),
    (
        (
            payment_context(),
            failure_question(),
            Answer(question_id="failure", raw_text="", skipped=True),
            "skipped",
        ),
        (
            payment_context(),
            failure_question(
                EvidenceCitation(kind="call", file_path=Path("outside.py"), symbol="client.send")
            ),
            Answer(question_id="failure", raw_text="client.send might fail."),
            "changed file",
        ),
        (
            payment_context(),
            failure_question(),
            Answer(question_id="failure", raw_text="The weather remains pleasant."),
            "repository concept",
        ),
    ),
)
def test_uncertain_assessments_explain_why_grounding_is_unavailable(
    context: ChangeContext,
    question: Question,
    answer: Answer,
    expected_note: str,
) -> None:
    assessment = HeuristicEvaluator().evaluate(context, question, answer)

    assert assessment.label is AssessmentLabel.UNCERTAIN
    assert assessment.confidence == "low"
    assert assessment.uncertainty_notes
    assert expected_note in " ".join(assessment.uncertainty_notes).lower()
    assert assessment.evidence or assessment.uncertainty_notes


def test_citations_are_filtered_deduplicated_and_keep_metadata_in_stable_order() -> None:
    outside = EvidenceCitation(
        kind="call",
        file_path=Path("vendor/client.py"),
        symbol="client.send",
        line_start=10,
    )
    question = failure_question(
        TRANSACTION_CITATION,
        outside,
        NETWORK_CITATION,
        TRANSACTION_CITATION,
    )

    assessment = HeuristicEvaluator().evaluate(
        payment_context(),
        question,
        Answer(
            question_id="failure",
            raw_text=("database.transaction does not undo processor.charge; retry needs recovery."),
        ),
    )

    assert assessment.evidence == (TRANSACTION_CITATION, NETWORK_CITATION)
    assert assessment.evidence[0].hunk_header == TRANSACTION_CITATION.hunk_header
    assert assessment.evidence[1].line_start == NETWORK_CITATION.line_start
    assert assessment.evidence[1].note == NETWORK_CITATION.note


def test_strong_evidence_answer_names_test_support_and_an_uncovered_path() -> None:
    question = Question(
        id="evidence",
        category=QuestionCategory.EVIDENCE,
        prompt="What does the changed test support, and what remains unverified?",
        rationale="The handler and its test changed.",
        references=(HANDLER_CITATION, TEST_CITATION),
    )

    assessment = HeuristicEvaluator().evaluate(
        payment_context(),
        question,
        Answer(
            question_id="evidence",
            raw_text=(
                "tests/payment_handler.test.ts verifies handlePayment returns an existing "
                "idempotency-key payment. It does not cover concurrent retries."
            ),
        ),
    )

    assert assessment.label is AssessmentLabel.STRONG
    assert assessment.evidence[:2] == (HANDLER_CITATION, TEST_CITATION)
    assert assessment.gaps == ()


def test_sparse_file_only_context_cannot_produce_strong() -> None:
    path = Path("src/settings.toml")
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/settings",
        base_branch="main",
        changed_files=(ChangedFile(path=path, status=FileStatus.MODIFIED),),
    )
    question = Question(
        id="behavior",
        category=QuestionCategory.BEHAVIOR,
        prompt="What changed?",
        rationale="The settings file changed.",
        references=(EvidenceCitation(kind="file", file_path=path),),
    )

    assessment = HeuristicEvaluator().evaluate(
        context,
        question,
        Answer(question_id="behavior", raw_text="The settings file changes settings."),
    )

    assert assessment.label in {AssessmentLabel.PARTIAL, AssessmentLabel.UNCERTAIN}


def test_summaries_avoid_punitive_or_runtime_certainty_language() -> None:
    assessment = HeuristicEvaluator().evaluate(
        payment_context(),
        failure_question(),
        Answer(question_id="failure", raw_text="processor.charge is a network call."),
    )

    summary = assessment.summary.lower()
    assert "incorrect" not in summary
    assert "pass" not in summary
    assert "fail" not in summary
    assert "score" not in summary
    assert "will definitely" not in summary


def test_mismatched_question_and_answer_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="question and answer IDs"):
        HeuristicEvaluator().evaluate(
            payment_context(),
            failure_question(),
            replace(Answer(question_id="failure", raw_text="retry"), question_id="behavior"),
        )
