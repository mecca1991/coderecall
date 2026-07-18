"""Integration coverage for the payment-idempotency demo context."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from coderecall.analysis import (
    ChangeModelBuilder,
    DiffSummaryService,
    FileFilter,
    QuestionGenerator,
    SideEffectDetector,
)
from coderecall.cli.app import app
from coderecall.core.types import QuestionCategory, SideEffectKind
from coderecall.git import DiffCollector, GitAdapter

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "payment_idempotency"
FIXTURE_FILES = (
    Path("src/payment_handler.ts"),
    Path("src/payment_service.ts"),
    Path("tests/payment_handler.test.ts"),
)


def run_git(directory: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=directory,
        capture_output=True,
        check=True,
        text=True,
    )


def commit_all(directory: Path, message: str) -> None:
    run_git(directory, "add", "--all")
    run_git(directory, "commit", "--quiet", "-m", message)


def test_detects_payment_processor_and_local_transaction_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_git(tmp_path, "init", "--quiet")
    run_git(tmp_path, "checkout", "--quiet", "-b", "main")
    run_git(tmp_path, "config", "user.name", "CodeRecall Tests")
    run_git(tmp_path, "config", "user.email", "tests@coderecall.local")
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "payment_handler.ts").write_text(
        "export async function handlePayment(request: PaymentRequest) {\n  return request;\n}\n"
    )
    (tmp_path / "src" / "payment_service.ts").write_text(
        "export async function capturePayment(input: PaymentInput) {\n  return input;\n}\n"
    )
    (tmp_path / "tests" / "payment_handler.test.ts").write_text(
        "it('handles a payment', async () => {\n  expect(true).toBe(true);\n});\n"
    )
    commit_all(tmp_path, "Add base payment flow")
    run_git(tmp_path, "checkout", "--quiet", "-b", "feature/payment-idempotency")

    for relative_path in FIXTURE_FILES:
        destination = tmp_path / relative_path
        destination.write_text((FIXTURE_ROOT / relative_path).read_text())
    commit_all(tmp_path, "Add payment idempotency handling")

    git = GitAdapter(tmp_path)
    repository = git.detect_repository()
    diff = DiffCollector(git, file_filter=FileFilter()).collect(repository, "main")
    context = ChangeModelBuilder(source_reader=git).build(repository, "main", diff)

    detected = SideEffectDetector().detect(context)

    network_effect = next(
        effect
        for effect in detected.likely_side_effects
        if effect.kind is SideEffectKind.NETWORK_CALL
    )
    transaction_effect = next(
        effect
        for effect in detected.likely_side_effects
        if effect.kind is SideEffectKind.TRANSACTION_BOUNDARY
    )
    assert network_effect.evidence[0].symbol == "processor.charge"
    assert transaction_effect.evidence[0].symbol == "database.transaction"
    assert network_effect.evidence[0].hunk_header is not None
    assert transaction_effect.evidence[0].hunk_header is not None
    assert network_effect.evidence != transaction_effect.evidence
    assert "external operations may not share" in transaction_effect.description
    assert detected.related_tests == (Path("tests/payment_handler.test.ts"),)

    summary = DiffSummaryService().summarize(detected)

    assert summary.relevant_files == (
        Path("src/payment_service.ts"),
        Path("src/payment_handler.ts"),
        Path("tests/payment_handler.test.ts"),
    )
    assert summary.tests == (Path("tests/payment_handler.test.ts"),)
    assert {effect.kind for effect in summary.side_effects} >= {
        SideEffectKind.NETWORK_CALL,
        SideEffectKind.TRANSACTION_BOUNDARY,
    }
    assert summary.purpose.startswith("Likely updates `handlePayment`")
    assert "network call" in summary.purpose
    assert "transaction boundary" in summary.purpose

    questions = QuestionGenerator().generate(detected)

    assert tuple(question.category for question in questions) == (
        QuestionCategory.BEHAVIOR,
        QuestionCategory.FAILURE,
        QuestionCategory.EVIDENCE,
    )
    assert "`handlePayment`" in questions[0].prompt
    assert "`processor.charge`" in questions[1].prompt
    assert "`database.transaction`" in questions[1].prompt
    assert "partial success" in questions[1].rationale
    assert '"tests/payment_handler.test.ts"' in questions[2].prompt
    assert {
        citation.file_path for question in questions for citation in question.references
    }.issubset(set(FIXTURE_FILES))

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app,
        ["review", "--base", "main", "--plain"],
        input=(
            "It adds an idempotency key.\n"
            "Retries reuse the stored payment result.\n"
            "\n"
            "\n"
            "The retry test checks that the processor is called once.\n"
            "\n"
        ),
    )

    assert result.exit_code == 0
    assert "Diff summary" in result.output
    assert '  - "src/payment_handler.ts"' in result.output
    assert '  - "src/payment_service.ts"' in result.output
    assert "Tests found:" in result.output
    assert '  - "tests/payment_handler.test.ts"' in result.output
    assert "Likely side effects:" in result.output
    assert "network call:" in result.output
    assert "transaction boundary:" in result.output
    assert result.output.index("Diff summary") < result.output.index("Question 1 of 3 [behavior]")
    assert "Question 2 of 3 [failure]" in result.output
    assert "Question 3 of 3 [evidence]" in result.output
    assert "Answers: 2 answered, 1 skipped" in result.output
