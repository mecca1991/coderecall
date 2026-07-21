"""Tests for branch-specific question generation."""

from pathlib import Path

import pytest

from coderecall.analysis import QuestionGenerator
from coderecall.core.errors import QuestionGenerationUnavailable
from coderecall.core.types import (
    ChangeContext,
    ChangedFile,
    ChangedSymbol,
    DiffHunk,
    EvidenceCitation,
    FileStatus,
    LikelySideEffect,
    QuestionCategory,
    SideEffectKind,
    SymbolOrigin,
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


def test_prefers_structured_symbols_over_fallback_symbols_within_source_files() -> None:
    fallback_path = Path("lib/status.dart")
    structured_path = Path("src/orders.py")
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/structured-preference",
        base_branch="main",
        changed_files=(
            ChangedFile(path=fallback_path, status=FileStatus.MODIFIED),
            ChangedFile(path=structured_path, status=FileStatus.MODIFIED),
        ),
        changed_symbols=(
            ChangedSymbol(
                file_path=fallback_path,
                name="refreshStatus",
                kind="function",
                origin=SymbolOrigin.HUNK_CONTEXT_FALLBACK,
            ),
            ChangedSymbol(
                file_path=structured_path,
                name="process_order",
                kind="function",
            ),
        ),
    )

    questions = QuestionGenerator().generate(context)

    assert all('`process_order` in "src/orders.py"' in question.prompt for question in questions)
    assert all("refreshStatus" not in question.prompt for question in questions)


def test_preserves_source_preference_when_only_test_symbol_is_structured() -> None:
    test_path = Path("tests/test_status.py")
    source_path = Path("lib/status.dart")
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/source-preference",
        base_branch="main",
        changed_files=(
            ChangedFile(path=test_path, status=FileStatus.MODIFIED, is_test=True),
            ChangedFile(path=source_path, status=FileStatus.MODIFIED),
        ),
        changed_symbols=(
            ChangedSymbol(
                file_path=test_path,
                name="test_refresh_status",
                kind="function",
            ),
            ChangedSymbol(
                file_path=source_path,
                name="refreshStatus",
                kind="function",
                line_start=12,
                origin=SymbolOrigin.HUNK_CONTEXT_FALLBACK,
            ),
        ),
    )

    questions = QuestionGenerator().generate(context)

    assert all('`refreshStatus` in "lib/status.dart"' in question.prompt for question in questions)
    assert all("test_refresh_status" not in question.prompt for question in questions)


def test_uses_fallback_symbol_and_file_in_all_questions_when_it_is_only_evidence() -> None:
    source_path = Path("lib/status.dart")
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/fallback-only",
        base_branch="main",
        changed_files=(ChangedFile(path=source_path, status=FileStatus.MODIFIED),),
        changed_symbols=(
            ChangedSymbol(
                file_path=source_path,
                name="refreshStatus",
                kind="function",
                line_start=12,
                origin=SymbolOrigin.HUNK_CONTEXT_FALLBACK,
            ),
        ),
    )

    questions = QuestionGenerator().generate(context)

    assert all('`refreshStatus` in "lib/status.dart"' in question.prompt for question in questions)
    assert all(
        question.references
        == (
            EvidenceCitation(
                kind="symbol",
                file_path=source_path,
                symbol="refreshStatus",
                line_start=12,
            ),
        )
        for question in questions
    )


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


def test_excludes_documentation_from_all_question_candidates() -> None:
    documentation_path = Path("docs/release-plan.py")
    source_path = Path("src/orders.py")
    test_path = Path("tests/test_orders.py")
    documentation_effect = EvidenceCitation(
        kind="call",
        file_path=documentation_path,
        symbol="client.post",
    )
    source_effect = EvidenceCitation(
        kind="call",
        file_path=source_path,
        symbol="database.update",
    )
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/order-processing",
        base_branch="main",
        changed_files=(
            ChangedFile(
                path=documentation_path,
                status=FileStatus.MODIFIED,
                is_documentation=True,
            ),
            ChangedFile(path=source_path, status=FileStatus.MODIFIED),
            ChangedFile(path=test_path, status=FileStatus.ADDED, is_test=True),
        ),
        changed_symbols=(
            ChangedSymbol(
                file_path=documentation_path,
                name="synthetic_documentation_symbol",
                kind="function",
            ),
            ChangedSymbol(
                file_path=source_path,
                name="process_order",
                kind="function",
                line_start=12,
            ),
        ),
        related_tests=(documentation_path, test_path),
        likely_side_effects=(
            LikelySideEffect(
                kind=SideEffectKind.NETWORK_CALL,
                description="Documentation contains a synthetic network signal.",
                evidence=(documentation_effect,),
            ),
            LikelySideEffect(
                kind=SideEffectKind.DATABASE_WRITE,
                description="Source contains a database signal.",
                evidence=(source_effect,),
            ),
        ),
    )

    questions = QuestionGenerator().generate(context)

    assert '`process_order` in "src/orders.py"' in questions[0].prompt
    assert "likely database write" in questions[1].prompt
    assert '`database.update` in "src/orders.py"' in questions[1].prompt
    assert '"tests/test_orders.py"' in questions[2].prompt
    assert '`process_order` in "src/orders.py"' in questions[2].prompt
    assert all(
        citation.file_path != documentation_path
        for question in questions
        for citation in question.references
    )


def test_uses_changed_test_when_it_is_the_only_eligible_subject() -> None:
    documentation_path = Path("README.md")
    test_path = Path("tests/test_checkout.dart")
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/test-only",
        base_branch="main",
        changed_files=(
            ChangedFile(
                path=documentation_path,
                status=FileStatus.MODIFIED,
                is_documentation=True,
            ),
            ChangedFile(path=test_path, status=FileStatus.MODIFIED, is_test=True),
        ),
        related_tests=(test_path,),
        diff_hunks=(
            DiffHunk(file_path=documentation_path, header="@@ -1 +1 @@"),
            DiffHunk(file_path=test_path, header="@@ -1 +1 @@"),
        ),
    )

    questions = QuestionGenerator().generate(context)

    assert all('"tests/test_checkout.dart"' in question.prompt for question in questions)
    assert all(
        citation.file_path == test_path
        for question in questions
        for citation in question.references
    )


def test_refuses_documentation_only_context_with_specific_reason() -> None:
    documentation_path = Path("docs/release-plan.md")
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/docs-only",
        base_branch="main",
        changed_files=(
            ChangedFile(
                path=documentation_path,
                status=FileStatus.MODIFIED,
                is_documentation=True,
            ),
        ),
        changed_symbols=(
            ChangedSymbol(
                file_path=documentation_path,
                name="synthetic_documentation_symbol",
                kind="function",
            ),
        ),
    )

    with pytest.raises(
        QuestionGenerationUnavailable,
        match="Changed files contain only documentation or planning changes\\.",
    ):
        QuestionGenerator().generate(context)
