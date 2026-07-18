"""Tests for building a structured change context."""

from __future__ import annotations

from pathlib import Path

from coderecall.analysis.change_model_builder import ChangeModelBuilder
from coderecall.core.types import (
    ChangedFile,
    DiffCollection,
    DiffHunk,
    FileStatus,
    FilteredFile,
    FilterReason,
    RepositoryContext,
)


def test_builds_context_without_losing_diff_evidence() -> None:
    hunk = DiffHunk(
        file_path=Path("src/payments.py"),
        header="@@ -1,2 +1,2 @@",
        old_start=1,
        old_lines=2,
        new_start=1,
        new_lines=2,
        patch="@@ -1,2 +1,2 @@\n-ENABLED = False\n+ENABLED = True\n",
    )
    changed_file = ChangedFile(
        path=Path("src/payments.py"),
        status=FileStatus.MODIFIED,
        language="python",
        hunks=(hunk,),
    )
    filtered_file = FilteredFile(
        path=Path("package-lock.json"),
        status=FileStatus.MODIFIED,
        reason=FilterReason.LOCKFILE,
    )
    diff = DiffCollection(
        merge_base="abc123",
        changed_files=(changed_file,),
        filtered_files=(filtered_file,),
        diff_hunks=(hunk,),
        uncertainty_notes=("An oversized file was skipped.",),
    )
    repository = RepositoryContext(
        root=Path("/repo"),
        current_branch="feature/payment-idempotency",
    )

    context = ChangeModelBuilder().build(repository, "main", diff)

    assert context.repo_root == Path("/repo")
    assert context.current_branch == "feature/payment-idempotency"
    assert context.base_branch == "main"
    assert context.merge_base == "abc123"
    assert context.changed_files == (changed_file,)
    assert context.filtered_files == (filtered_file,)
    assert context.diff_hunks == (hunk,)
    assert context.changed_symbols == ()
    assert context.nearby_imports == ()
    assert context.call_sites == ()
    assert context.related_tests == ()
    assert context.uncertainty_notes == ("An oversized file was skipped.",)


def test_classifies_languages_and_changed_tests_in_diff_order() -> None:
    changed_files = (
        ChangedFile(path=Path("src/payments.py"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("tests/unit/test_payments.py"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("web/checkout.tsx"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("web/__tests__/checkout.test.ts"), status=FileStatus.ADDED),
        ChangedFile(path=Path("src/contest.py"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("notes/change.txt"), status=FileStatus.ADDED),
    )
    diff = DiffCollection(merge_base="abc123", changed_files=changed_files)
    repository = RepositoryContext(root=Path("/repo"), current_branch="feature/checkout")

    context = ChangeModelBuilder().build(repository, "main", diff)

    by_path = {changed.path: changed for changed in context.changed_files}
    assert by_path[Path("src/payments.py")].language == "python"
    assert by_path[Path("tests/unit/test_payments.py")].language == "python"
    assert by_path[Path("web/checkout.tsx")].language == "typescript"
    assert by_path[Path("web/__tests__/checkout.test.ts")].language == "typescript"
    assert by_path[Path("notes/change.txt")].language is None
    assert by_path[Path("tests/unit/test_payments.py")].is_test is True
    assert by_path[Path("web/__tests__/checkout.test.ts")].is_test is True
    assert by_path[Path("src/contest.py")].is_test is False
    assert context.related_tests == (
        Path("tests/unit/test_payments.py"),
        Path("web/__tests__/checkout.test.ts"),
    )


def test_recognizes_test_filename_conventions_without_substring_matches() -> None:
    changed_files = (
        ChangedFile(path=Path("src/payment_test.py"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("web/checkout.spec.js"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("web/checkout.test.mjs"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("src/testimonial.py"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("web/specification.ts"), status=FileStatus.MODIFIED),
    )
    diff = DiffCollection(merge_base="abc123", changed_files=changed_files)
    repository = RepositoryContext(root=Path("/repo"), current_branch="feature/tests")

    context = ChangeModelBuilder().build(repository, "main", diff)

    assert context.related_tests == (
        Path("src/payment_test.py"),
        Path("web/checkout.spec.js"),
        Path("web/checkout.test.mjs"),
    )
