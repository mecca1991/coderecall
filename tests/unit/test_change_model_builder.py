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
