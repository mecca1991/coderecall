"""Integration tests for collecting real Git branch changes."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from coderecall.analysis import FileFilter
from coderecall.core.errors import DiffCollectionFailed
from coderecall.core.types import DiffCollection, FileStatus
from coderecall.git import DiffCollector, GitAdapter


def run_git(directory: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=directory,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def initialize_repository(directory: Path) -> None:
    run_git(directory, "init", "--quiet")
    run_git(directory, "checkout", "--quiet", "-b", "main")
    run_git(directory, "config", "user.name", "CodeRecall Tests")
    run_git(directory, "config", "user.email", "tests@coderecall.local")


def commit_all(directory: Path, message: str) -> None:
    run_git(directory, "add", "--all")
    run_git(directory, "commit", "--quiet", "-m", message)


def collect_changes(
    directory: Path,
    *,
    include_uncommitted: bool = False,
    max_patch_bytes: int = 1_000_000,
    max_total_patch_bytes: int = 16 * 1024 * 1024,
    max_changed_files: int = 1_000,
    max_raw_changed_files: int = 10_000,
    filter_low_signal: bool = False,
) -> DiffCollection:
    git = GitAdapter(directory)
    repository = git.detect_repository()
    return DiffCollector(
        git,
        max_patch_bytes=max_patch_bytes,
        max_total_patch_bytes=max_total_patch_bytes,
        max_changed_files=max_changed_files,
        max_raw_changed_files=max_raw_changed_files,
        file_filter=FileFilter() if filter_low_signal else None,
    ).collect(
        repository,
        "main",
        include_uncommitted=include_uncommitted,
    )


def test_collects_added_modified_deleted_and_renamed_files(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    (tmp_path / "modified.py").write_text("def value():\n    return 1\n")
    (tmp_path / "deleted.txt").write_text("remove me\n")
    (tmp_path / "rename-before.txt").write_text("preserve me\n")
    commit_all(tmp_path, "Base files")
    run_git(tmp_path, "checkout", "--quiet", "-b", "feature/change-shapes")

    (tmp_path / "modified.py").write_text("def value():\n    return 2\n")
    (tmp_path / "added file.py").write_text("ENABLED = True\n")
    (tmp_path / "deleted.txt").unlink()
    run_git(tmp_path, "mv", "rename-before.txt", "rename-after.txt")
    commit_all(tmp_path, "Exercise every file status")

    collection = collect_changes(tmp_path)
    by_path = {changed.path: changed for changed in collection.changed_files}

    assert by_path[Path("added file.py")].status is FileStatus.ADDED
    assert by_path[Path("modified.py")].status is FileStatus.MODIFIED
    assert by_path[Path("deleted.txt")].status is FileStatus.DELETED
    renamed = by_path[Path("rename-after.txt")]
    assert renamed.status is FileStatus.RENAMED
    assert renamed.old_path == Path("rename-before.txt")
    assert by_path[Path("added file.py")].hunks
    assert by_path[Path("modified.py")].hunks
    assert by_path[Path("deleted.txt")].hunks
    assert collection.diff_hunks == tuple(
        hunk for changed_file in collection.changed_files for hunk in changed_file.hunks
    )
    assert collection.merge_base == run_git(tmp_path, "rev-parse", "main")


def test_uncommitted_collection_includes_only_tracked_files(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    (tmp_path / "committed.txt").write_text("base\n")
    (tmp_path / "staged.txt").write_text("base\n")
    (tmp_path / "unstaged.txt").write_text("base\n")
    commit_all(tmp_path, "Base files")
    run_git(tmp_path, "checkout", "--quiet", "-b", "feature/working-tree")

    (tmp_path / "committed.txt").write_text("committed branch change\n")
    commit_all(tmp_path, "Committed branch change")
    (tmp_path / "staged.txt").write_text("staged change\n")
    run_git(tmp_path, "add", "staged.txt")
    (tmp_path / "unstaged.txt").write_text("unstaged change\n")
    (tmp_path / "untracked.txt").write_text("not collected\n")

    committed_only = collect_changes(tmp_path)
    with_working_tree = collect_changes(tmp_path, include_uncommitted=True)

    assert {changed.path for changed in committed_only.changed_files} == {Path("committed.txt")}
    assert {changed.path for changed in with_working_tree.changed_files} == {
        Path("committed.txt"),
        Path("staged.txt"),
        Path("unstaged.txt"),
    }
    assert with_working_tree.includes_uncommitted is True


def test_file_to_directory_replacement_keeps_hunks_attributed(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    (tmp_path / "module").write_text("old standalone module\n")
    commit_all(tmp_path, "Add standalone module")
    run_git(tmp_path, "checkout", "--quiet", "-b", "feature/module-package")

    (tmp_path / "module").unlink()
    (tmp_path / "module").mkdir()
    (tmp_path / "module" / "child.py").write_text("NEW_VALUE = 42\n")
    commit_all(tmp_path, "Replace module with package")

    collection = collect_changes(tmp_path)
    by_path = {changed.path: changed for changed in collection.changed_files}

    deleted = by_path[Path("module")]
    added = by_path[Path("module/child.py")]
    assert deleted.status is FileStatus.DELETED
    assert added.status is FileStatus.ADDED
    assert len(deleted.hunks) == 1
    assert len(added.hunks) == 1
    assert "old standalone module" in deleted.hunks[0].patch
    assert "NEW_VALUE" not in deleted.hunks[0].patch
    assert "NEW_VALUE" in added.hunks[0].patch
    assert all(hunk.file_path == Path("module") for hunk in deleted.hunks)
    assert all(hunk.file_path == Path("module/child.py") for hunk in added.hunks)


def test_binary_marker_in_text_content_does_not_discard_hunks(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    (tmp_path / "format-notes.txt").write_text("Git patch notes\n")
    commit_all(tmp_path, "Add format notes")
    run_git(tmp_path, "checkout", "--quiet", "-b", "feature/document-binary-marker")

    (tmp_path / "format-notes.txt").write_text("Git patch notes\nGIT binary patch\n")
    commit_all(tmp_path, "Document binary marker")

    collection = collect_changes(tmp_path)
    changed_file = collection.changed_files[0]

    assert changed_file.is_binary is False
    assert changed_file.hunks
    assert "GIT binary patch" in changed_file.hunks[0].patch


def test_unicode_line_separator_does_not_create_fabricated_hunk(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    (tmp_path / "unicode.txt").write_text("base\n")
    commit_all(tmp_path, "Add Unicode fixture")
    run_git(tmp_path, "checkout", "--quiet", "-b", "feature/unicode-separator")

    (tmp_path / "unicode.txt").write_text("prefix\u2028@@ -1 +1 @@\n")
    commit_all(tmp_path, "Add Unicode line separator")

    collection = collect_changes(tmp_path)
    changed_file = collection.changed_files[0]

    assert len(changed_file.hunks) == 1
    assert "prefix\u2028@@ -1 +1 @@" in changed_file.hunks[0].patch


def test_aggregate_patch_and_file_count_limits_are_enforced(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    for index in range(3):
        (tmp_path / f"file-{index}.txt").write_text("base\n")
    commit_all(tmp_path, "Add aggregate fixtures")
    run_git(tmp_path, "checkout", "--quiet", "-b", "feature/aggregate-limit")

    for index in range(3):
        (tmp_path / f"file-{index}.txt").write_text(str(index) * 400 + "\n")
    commit_all(tmp_path, "Expand aggregate fixtures")

    collection = collect_changes(
        tmp_path,
        max_patch_bytes=2_000,
        max_total_patch_bytes=800,
    )

    assert any(changed_file.hunks for changed_file in collection.changed_files)
    assert any(not changed_file.hunks for changed_file in collection.changed_files)
    assert any(
        "buffered patch data exceeds 800 bytes" in note for note in collection.uncertainty_notes
    )

    with pytest.raises(DiffCollectionFailed) as captured:
        collect_changes(tmp_path, max_changed_files=2)

    assert "more than 2 changed files" in captured.value.message


def test_filters_generated_files_before_analysis_limits(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("VALUE = 1\n")
    commit_all(tmp_path, "Add application source")
    run_git(tmp_path, "checkout", "--quiet", "-b", "feature/generated-output")

    (tmp_path / "src" / "app.py").write_text("VALUE = 2\n")
    (tmp_path / "dist").mkdir()
    for index in range(3):
        (tmp_path / "dist" / f"bundle-{index}.js").write_text("x" * 500 + "\n")
    commit_all(tmp_path, "Build application")

    collection = collect_changes(
        tmp_path,
        max_changed_files=1,
        max_total_patch_bytes=300,
        filter_low_signal=True,
    )

    assert [changed.path for changed in collection.changed_files] == [Path("src/app.py")]
    assert collection.changed_files[0].hunks
    assert {filtered.path for filtered in collection.filtered_files} == {
        Path("dist/bundle-0.js"),
        Path("dist/bundle-1.js"),
        Path("dist/bundle-2.js"),
    }
    assert not collection.uncertainty_notes


def test_keeps_cross_boundary_rename_in_analysis(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.js").write_text("export const enabled = true;\n")
    commit_all(tmp_path, "Add source file")
    run_git(tmp_path, "checkout", "--quiet", "-b", "feature/move-generated-file")

    (tmp_path / "dist").mkdir()
    run_git(tmp_path, "mv", "src/app.js", "dist/app.js")
    commit_all(tmp_path, "Move source into generated output")

    collection = collect_changes(tmp_path, filter_low_signal=True)

    assert len(collection.changed_files) == 1
    renamed = collection.changed_files[0]
    assert renamed.status is FileStatus.RENAMED
    assert renamed.old_path == Path("src/app.js")
    assert renamed.path == Path("dist/app.js")
    assert collection.filtered_files == ()


def test_raw_changed_file_limit_still_applies_before_filtering(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    (tmp_path / "base.txt").write_text("base\n")
    commit_all(tmp_path, "Add base file")
    run_git(tmp_path, "checkout", "--quiet", "-b", "feature/generated-output")

    (tmp_path / "dist").mkdir()
    for index in range(3):
        (tmp_path / "dist" / f"bundle-{index}.js").write_text("generated\n")
    commit_all(tmp_path, "Build application")

    with pytest.raises(DiffCollectionFailed) as captured:
        collect_changes(
            tmp_path,
            max_changed_files=1,
            max_raw_changed_files=2,
            filter_low_signal=True,
        )

    assert "more than 2 raw changed files" in captured.value.message


def test_submodule_patch_format_ignores_repository_configuration(tmp_path: Path) -> None:
    dependency = tmp_path / "dependency-repository"
    project = tmp_path / "project"
    dependency.mkdir()
    project.mkdir()
    initialize_repository(dependency)
    (dependency / "version.txt").write_text("version 1\n")
    commit_all(dependency, "Dependency version 1")

    initialize_repository(project)
    run_git(
        project,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "--quiet",
        str(dependency),
        "vendor/dependency",
    )
    commit_all(project, "Add dependency submodule")
    run_git(project, "checkout", "--quiet", "-b", "feature/update-dependency")

    checked_out_dependency = project / "vendor" / "dependency"
    run_git(checked_out_dependency, "config", "user.name", "CodeRecall Tests")
    run_git(checked_out_dependency, "config", "user.email", "tests@coderecall.local")
    (checked_out_dependency / "version.txt").write_text("version 2\n")
    commit_all(checked_out_dependency, "Dependency version 2")
    commit_all(project, "Update dependency submodule")

    for configured_format in ("log", "diff"):
        run_git(project, "config", "diff.submodule", configured_format)
        collection = collect_changes(project)

        assert len(collection.changed_files) == 1
        changed_file = collection.changed_files[0]
        assert changed_file.path == Path("vendor/dependency")
        assert changed_file.status is FileStatus.MODIFIED
        assert len(changed_file.hunks) == 1


def test_binary_and_large_patches_are_reported_without_hunks(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    (tmp_path / "asset.bin").write_bytes(b"\x00base\x01")
    (tmp_path / "large.txt").write_text("base\n")
    commit_all(tmp_path, "Base files")
    run_git(tmp_path, "checkout", "--quiet", "-b", "feature/large-files")

    (tmp_path / "asset.bin").write_bytes(b"\x00changed\x02")
    (tmp_path / "large.txt").write_text("x" * 200_000)
    commit_all(tmp_path, "Change binary and large files")

    git = GitAdapter(tmp_path)
    repository = git.detect_repository()
    merge_base = git.find_merge_base(repository, "main")
    raw_diff = git.collect_diff(
        repository,
        merge_base,
        max_patch_bytes=200,
        max_total_patch_bytes=1_000,
        max_changed_files=10,
    )
    collection = DiffCollector(git, max_patch_bytes=200).collect(repository, "main")
    by_path = {changed.path: changed for changed in collection.changed_files}

    assert raw_diff.patch_records[-1].oversized is True
    assert raw_diff.patch_records[-1].content is None
    assert by_path[Path("asset.bin")].is_binary is True
    assert by_path[Path("asset.bin")].hunks == ()
    assert by_path[Path("large.txt")].hunks == ()
    assert any('binary file "asset.bin"' in note for note in collection.uncertainty_notes)
    assert any(
        '"large.txt"' in note and "exceeds 200 bytes" in note
        for note in collection.uncertainty_notes
    )
