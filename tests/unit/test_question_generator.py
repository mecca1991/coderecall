"""Tests for branch-specific question generation."""

from pathlib import Path

import pytest

from coderecall.analysis import QuestionGenerator
from coderecall.core.errors import QuestionGenerationUnavailable
from coderecall.core.types import (
    ChangeContext,
    ChangedFile,
    ChangedSymbol,
    EvidenceCitation,
    FileStatus,
    LikelySideEffect,
    QuestionCategory,
    SideEffectKind,
)


def test_generates_three_branch_specific_questions_in_stable_order() -> None:
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/order-processing",
        base_branch="main",
        changed_files=(ChangedFile(path=Path("src/orders.py"), status=FileStatus.MODIFIED),),
        changed_symbols=(
            ChangedSymbol(
                file_path=Path("src/orders.py"),
                name="process_order",
                kind="function",
                line_start=12,
            ),
        ),
    )

    questions = QuestionGenerator().generate(context)

    assert tuple(question.id for question in questions) == ("behavior", "failure", "evidence")
    assert tuple(question.category for question in questions) == (
        QuestionCategory.BEHAVIOR,
        QuestionCategory.FAILURE,
        QuestionCategory.EVIDENCE,
    )
    assert all("`process_order`" in question.prompt for question in questions)
    assert all('"src/orders.py"' in question.prompt for question in questions)
    assert all(question.rationale for question in questions)
    assert all(question.references for question in questions)
    assert {citation.file_path for question in questions for citation in question.references} == {
        Path("src/orders.py")
    }
    assert questions == QuestionGenerator().generate(context)


def test_refuses_to_generate_generic_questions_without_meaningful_files() -> None:
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/generated-only",
        base_branch="main",
    )

    with pytest.raises(QuestionGenerationUnavailable, match="meaningful changed file"):
        QuestionGenerator().generate(context)


def test_failure_question_connects_external_effect_and_transaction_boundary() -> None:
    service_path = Path("src/payment_service.ts")
    network_citation = EvidenceCitation(
        kind="call",
        file_path=service_path,
        symbol="processor.charge",
        hunk_header="@@ -4,3 +4,8 @@ capturePayment",
        line_start=7,
    )
    transaction_citation = EvidenceCitation(
        kind="call",
        file_path=service_path,
        symbol="database.transaction",
        hunk_header="@@ -4,3 +4,8 @@ capturePayment",
        line_start=6,
    )
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/payment-idempotency",
        base_branch="main",
        changed_files=(ChangedFile(path=service_path, status=FileStatus.MODIFIED),),
        changed_symbols=(
            ChangedSymbol(
                file_path=service_path,
                name="capturePayment",
                kind="function",
                line_start=5,
            ),
        ),
        likely_side_effects=(
            LikelySideEffect(
                kind=SideEffectKind.NETWORK_CALL,
                description="The change likely makes an external call.",
                evidence=(
                    EvidenceCitation(
                        kind="call",
                        file_path=Path("outside.ts"),
                        symbol="unrelated.send",
                    ),
                    network_citation,
                ),
            ),
            LikelySideEffect(
                kind=SideEffectKind.TRANSACTION_BOUNDARY,
                description="The change likely creates a local transaction boundary.",
                evidence=(transaction_citation,),
            ),
        ),
    )

    failure = QuestionGenerator().generate(context)[1]

    assert "network call" in failure.prompt
    assert "`processor.charge`" in failure.prompt
    assert "transaction boundary" in failure.prompt
    assert "`database.transaction`" in failure.prompt
    assert "retry or recovery" in failure.prompt
    assert "partial success" in failure.rationale
    assert failure.references == (network_citation, transaction_citation)


def test_does_not_pair_side_effects_from_unrelated_changed_files() -> None:
    client_path = Path("src/client.py")
    worker_path = Path("src/worker.py")
    network_citation = EvidenceCitation(
        kind="call",
        file_path=client_path,
        symbol="client.post",
        hunk_header="@@ -2,3 +2,4 @@ send_event",
        line_start=4,
    )
    transaction_citation = EvidenceCitation(
        kind="call",
        file_path=worker_path,
        symbol="session.begin",
        hunk_header="@@ -8,3 +8,5 @@ process_job",
        line_start=10,
    )
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/unrelated-effects",
        base_branch="main",
        changed_files=(
            ChangedFile(path=client_path, status=FileStatus.MODIFIED),
            ChangedFile(path=worker_path, status=FileStatus.MODIFIED),
        ),
        changed_symbols=(
            ChangedSymbol(
                file_path=worker_path,
                name="process_job",
                kind="function",
                line_start=8,
            ),
        ),
        likely_side_effects=(
            LikelySideEffect(
                kind=SideEffectKind.NETWORK_CALL,
                description="The change likely makes an external call.",
                evidence=(network_citation,),
            ),
            LikelySideEffect(
                kind=SideEffectKind.TRANSACTION_BOUNDARY,
                description="The change likely creates a transaction boundary.",
                evidence=(transaction_citation,),
            ),
        ),
    )

    failure = QuestionGenerator().generate(context)[1]

    assert "succeeds but" not in failure.prompt
    assert "partial success" not in failure.rationale
    assert "`process_job`" not in failure.prompt
    assert "likely network call" in failure.prompt
    assert failure.references == (network_citation,)


def test_evidence_question_names_a_changed_test_and_ignores_other_paths() -> None:
    source_path = Path("src/orders.py")
    test_path = Path("tests/test_orders.py")
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/order-processing",
        base_branch="main",
        changed_files=(
            ChangedFile(path=source_path, status=FileStatus.MODIFIED),
            ChangedFile(path=test_path, status=FileStatus.ADDED, is_test=True),
        ),
        changed_symbols=(
            ChangedSymbol(
                file_path=source_path,
                name="process_order",
                kind="function",
                line_start=12,
            ),
        ),
        related_tests=(Path("outside_test.py"), test_path),
    )

    evidence = QuestionGenerator().generate(context)[2]

    assert '"tests/test_orders.py"' in evidence.prompt
    assert "`process_order`" in evidence.prompt
    assert "if any" in evidence.prompt
    assert "which important path remains unverified" in evidence.prompt
    assert "related test" not in evidence.rationale
    assert evidence.references == (
        EvidenceCitation(
            kind="symbol",
            file_path=source_path,
            symbol="process_order",
            line_start=12,
        ),
        EvidenceCitation(kind="test", file_path=test_path),
    )


def test_prefers_a_non_test_symbol_for_the_primary_changed_area() -> None:
    test_path = Path("a_tests/test_orders.py")
    source_path = Path("src/orders.py")
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/order-processing",
        base_branch="main",
        changed_files=(
            ChangedFile(path=test_path, status=FileStatus.ADDED, is_test=True),
            ChangedFile(path=source_path, status=FileStatus.MODIFIED),
        ),
        changed_symbols=(
            ChangedSymbol(
                file_path=test_path,
                name="test_process_order",
                kind="function",
                line_start=4,
            ),
            ChangedSymbol(
                file_path=source_path,
                name="process_order",
                kind="function",
                line_start=12,
            ),
        ),
        related_tests=(test_path,),
    )

    questions = QuestionGenerator().generate(context)

    assert all("`process_order`" in question.prompt for question in questions)
    assert all("`test_process_order`" not in question.prompt for question in questions)


def test_behavior_question_describes_a_deleted_symbol_as_removed() -> None:
    source_path = Path("src/legacy.py")
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/remove-legacy",
        base_branch="main",
        changed_files=(ChangedFile(path=source_path, status=FileStatus.DELETED),),
        changed_symbols=(
            ChangedSymbol(
                file_path=source_path,
                name="legacy_handler",
                kind="function",
            ),
        ),
    )

    behavior = QuestionGenerator().generate(context)[0]

    assert "removing `legacy_handler`" in behavior.prompt
    assert "eliminate" in behavior.prompt
    assert "introduce or modify" not in behavior.prompt
    assert "removes" in behavior.rationale


def test_refuses_binary_only_context_without_analyzable_evidence() -> None:
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/update-image",
        base_branch="main",
        changed_files=(
            ChangedFile(
                path=Path("assets/diagram.png"),
                status=FileStatus.MODIFIED,
                is_binary=True,
            ),
        ),
        uncertainty_notes=("The binary patch could not be inspected.",),
    )

    with pytest.raises(QuestionGenerationUnavailable, match="analyzable change evidence"):
        QuestionGenerator().generate(context)
