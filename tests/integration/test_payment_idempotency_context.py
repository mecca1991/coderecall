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
from coderecall.core.types import Answer, AssessmentLabel, QuestionCategory, SideEffectKind
from coderecall.evaluation import FollowUpSelector, HeuristicEvaluator
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

    answers = (
        Answer(
            question_id="behavior",
            raw_text=(
                "handlePayment calls payments.findByIdempotencyKey and returns the existing "
                "payment before capturePayment."
            ),
        ),
        Answer(
            question_id="failure",
            raw_text=("database.transaction rollback undoes processor.charge, so retry is safe."),
        ),
        Answer(
            question_id="evidence",
            raw_text=(
                "tests/payment_handler.test.ts checks handlePayment returns the stored "
                "idempotency-key payment. It does not cover a processor failure after charging."
            ),
        ),
    )
    evaluator = HeuristicEvaluator()
    assessments = tuple(
        evaluator.evaluate(detected, question, answer)
        for question, answer in zip(questions, answers, strict=True)
    )

    assert tuple(assessment.label for assessment in assessments) == (
        AssessmentLabel.STRONG,
        AssessmentLabel.GAP_FOUND,
        AssessmentLabel.STRONG,
    )
    assert tuple(assessment.question_id for assessment in assessments) == (
        "behavior",
        "failure",
        "evidence",
    )
    assert {citation.symbol for citation in assessments[1].evidence} == {
        "processor.charge",
        "database.transaction",
    }

    follow_up = FollowUpSelector().select(detected, questions, assessments)

    assert follow_up is not None
    assert follow_up.question.id == "failure-follow-up"
    assert tuple(citation.symbol for citation in follow_up.question.references) == (
        "processor.charge",
        "database.transaction",
    )
    assert "processor.charge" in follow_up.question.prompt
    assert "database.transaction" in follow_up.question.prompt
    assert "reconciliation" in follow_up.question.prompt

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app,
        ["review", "--base", "main", "--plain"],
        input=(
            "handlePayment calls payments.findByIdempotencyKey and returns the existing "
            "payment before capturePayment.\n"
            "\n"
            "database.transaction rollback undoes processor.charge, so retry is safe.\n"
            "\n"
            "tests/payment_handler.test.ts checks handlePayment returns the stored "
            "idempotency-key payment. It does not cover a processor failure after charging.\n"
            "\n"
            "Use a durable idempotency record and reconcile pending processor charges.\n"
            "\n"
        ),
    )

    assert result.exit_code == 0
    assert result.output.startswith(
        "CodeRecall review\n"
        f'Repository: "{tmp_path}"\n'
        "Branch: feature/payment-idempotency -> main\n"
        f"Merge base: {diff.merge_base[:12]}\n"
        "Changes: 3 total, 3 analyzed, 0 filtered\n"
    )
    assert "Changed files:\n" in result.output
    assert '  - modified: "src/payment_handler.ts"' in result.output
    assert '  - modified: "src/payment_service.ts"' in result.output
    assert '  - modified: "tests/payment_handler.test.ts"' in result.output
    assert "\nChange summary\n" in result.output
    assert '  - "src/payment_handler.ts"' in result.output
    assert '  - "src/payment_service.ts"' in result.output
    assert "Tests found:" in result.output
    assert '  - "tests/payment_handler.test.ts"' in result.output
    assert "Likely side effects:" in result.output
    assert "network call:" in result.output
    assert "transaction boundary:" in result.output
    assert result.output.index("Change summary") < result.output.index(
        "Questions\nA blank line submits; press Enter immediately to skip."
    )
    assert "Question 1/3 — Behavior" in result.output
    assert "Question 2/3 — Failure" in result.output
    assert "Question 3/3 — Evidence" in result.output
    assert result.output.count("Follow-up\n") == 1
    assert "processor.charge" in result.output
    assert "database.transaction" in result.output
    assert "reconciliation" in result.output
    assert result.output.count("Answer:\n") == 4
    assert result.output.count("Answer recorded.\n") == 4
    assert result.output.count("Skipped.\n") == 0
    assert "\nSession complete\nAnswers: 4 answered, 0 skipped\n" in result.output
    assert result.output.endswith(f'Report written: "{tmp_path / "coderecall-report.md"}"\n')
    assert "handlePayment calls payments.findByIdempotencyKey" not in result.output
    assert "rollback undoes processor.charge" not in result.output
    assert "checks handlePayment returns" not in result.output
    assert "Explain the change:" not in result.output
    assert "\x1b" not in result.output

    report = (tmp_path / "coderecall-report.md").read_text(encoding="utf-8")
    assert "Revisit the rollback claim" in report
    assert "processor.charge" in report
    assert "database.transaction" in report
    assert "Use a durable idempotency record and reconcile pending processor charges." in report
    talking_points = report.split("## Review Talking Points\n\n", 1)[1]
    assert talking_points.startswith(f"- Explain the change: {summary.purpose}\n")
    assert "- Prepare to discuss: Revisit the rollback claim" in talking_points
    assert "- Evidence to cite: `tests/payment_handler.test.ts`." in talking_points
    assert "No review talking points generated." not in talking_points

    disabled = CliRunner().invoke(
        app,
        ["review", "--base", "main", "--plain", "--no-follow-up"],
        input=(
            "handlePayment calls payments.findByIdempotencyKey and returns the existing "
            "payment before capturePayment.\n\n"
            "database.transaction rollback undoes processor.charge, so retry is safe.\n\n"
            "tests/payment_handler.test.ts checks handlePayment returns the stored "
            "idempotency-key payment. It does not cover a processor failure after charging.\n\n"
        ),
    )

    assert disabled.exit_code == 0
    assert "Follow-up\n" not in disabled.output
    assert "\nSession complete\nAnswers: 3 answered, 0 skipped\n" in disabled.output
    assert disabled.output.endswith(f'Report written: "{tmp_path / "coderecall-report.md"}"\n')

    all_strong = CliRunner().invoke(
        app,
        ["review", "--base", "main", "--plain"],
        input=(
            "handlePayment calls payments.findByIdempotencyKey and returns the existing "
            "payment before capturePayment.\n\n"
            "processor.charge can succeed before database.transaction rolls back. A retry "
            "could charge again, so the flow needs idempotency or reconciliation.\n\n"
            "tests/payment_handler.test.ts checks handlePayment returns the stored "
            "idempotency-key payment. It does not cover a processor failure after charging.\n\n"
        ),
    )

    assert all_strong.exit_code == 0
    assert "Follow-up\n" not in all_strong.output
    assert "\nSession complete\nAnswers: 3 answered, 0 skipped\n" in all_strong.output
    assert all_strong.output.endswith(f'Report written: "{tmp_path / "coderecall-report.md"}"\n')
