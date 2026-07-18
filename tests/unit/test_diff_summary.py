"""Tests for deterministic diff summaries."""

from pathlib import Path

from coderecall.analysis import DiffSummaryService
from coderecall.core.types import (
    ChangeContext,
    ChangedFile,
    ChangedSymbol,
    EvidenceCitation,
    FileStatus,
    LikelySideEffect,
    SideEffectKind,
)


def test_ranks_relevant_files_and_caps_them_at_five() -> None:
    changed_files = (
        ChangedFile(path=Path("src/plain.py"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("tests/test_plain.py"), status=FileStatus.MODIFIED, is_test=True),
        ChangedFile(path=Path("src/symbol.py"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("src/effect.py"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("src/other.py"), status=FileStatus.ADDED),
        ChangedFile(path=Path("tests/test_other.py"), status=FileStatus.ADDED, is_test=True),
    )
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/summary",
        base_branch="main",
        changed_files=changed_files,
        changed_symbols=(
            ChangedSymbol(file_path=Path("src/symbol.py"), name="summarize", kind="function"),
        ),
        related_tests=(Path("tests/test_plain.py"), Path("tests/test_other.py")),
        likely_side_effects=(
            LikelySideEffect(
                kind=SideEffectKind.FILE_WRITE,
                description="The change may write to a local file.",
                evidence=(
                    EvidenceCitation(kind="call", file_path=Path("src/effect.py"), symbol="open"),
                ),
            ),
        ),
    )

    summary = DiffSummaryService().summarize(context)

    assert summary.relevant_files == (
        Path("src/effect.py"),
        Path("src/symbol.py"),
        Path("src/plain.py"),
        Path("src/other.py"),
        Path("tests/test_plain.py"),
    )
    assert summary.tests == (Path("tests/test_plain.py"), Path("tests/test_other.py"))


def test_builds_qualified_purpose_from_symbols_and_side_effects() -> None:
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/payment-idempotency",
        base_branch="main",
        changed_files=(
            ChangedFile(path=Path("src/payment.py"), status=FileStatus.MODIFIED),
            ChangedFile(path=Path("tests/test_payment.py"), status=FileStatus.ADDED, is_test=True),
        ),
        changed_symbols=(
            ChangedSymbol(
                file_path=Path("src/payment.py"),
                name="capture_payment",
                kind="function",
            ),
        ),
        likely_side_effects=(
            LikelySideEffect(
                kind=SideEffectKind.NETWORK_CALL,
                description="The change likely calls an external service.",
                evidence=(
                    EvidenceCitation(
                        kind="call",
                        file_path=Path("src/payment.py"),
                        symbol="processor.charge",
                    ),
                ),
            ),
        ),
    )

    summary = DiffSummaryService().summarize(context)

    assert summary.purpose == (
        "Likely updates `capture_payment` across 2 meaningful files, with a network call signal."
    )
    assert summary.side_effects == context.likely_side_effects


def test_sanitizes_summary_evidence_and_deduplicates_sections() -> None:
    valid_effect = LikelySideEffect(
        kind=SideEffectKind.DATABASE_WRITE,
        description="The change may write database state.",
        evidence=(
            EvidenceCitation(kind="call", file_path=Path("src/app.py"), symbol="session.add"),
            EvidenceCitation(kind="call", file_path=Path("outside.py"), symbol="session.add"),
        ),
    )
    unsupported_effect = LikelySideEffect(
        kind=SideEffectKind.FILE_WRITE,
        description="The change may write a file.",
        evidence=(EvidenceCitation(kind="call", file_path=Path("outside.py"), symbol="open"),),
    )
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/safe-summary",
        base_branch="main",
        changed_files=(ChangedFile(path=Path("src/app.py"), status=FileStatus.DELETED),),
        related_tests=(Path("outside_test.py"),),
        likely_side_effects=(valid_effect, valid_effect, unsupported_effect),
        uncertainty_notes=("Source was unavailable.", "Source was unavailable."),
    )

    summary = DiffSummaryService().summarize(context)

    assert summary.purpose == (
        "Likely removes code in 1 meaningful file, with a database write signal."
    )
    assert summary.tests == ()
    assert summary.side_effects == (
        LikelySideEffect(
            kind=SideEffectKind.DATABASE_WRITE,
            description=valid_effect.description,
            evidence=(valid_effect.evidence[0],),
        ),
    )
    assert summary.uncertainty_notes == ("Source was unavailable.",)


def test_summarizes_an_empty_analysis_set_without_claiming_behavior() -> None:
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/generated-output",
        base_branch="main",
        uncertainty_notes=("All changed files were filtered from analysis.",),
    )

    summary = DiffSummaryService().summarize(context)

    assert summary.purpose == "No meaningful code changes were available to summarize."
    assert summary.relevant_files == ()
    assert summary.uncertainty_notes == ("All changed files were filtered from analysis.",)
