"""Tests for excluding low-signal files from analysis."""

from __future__ import annotations

from pathlib import Path

import pytest

from coderecall.analysis.file_filter import FileFilter
from coderecall.core.types import ChangedFile, FileStatus, FilterReason


@pytest.mark.parametrize(
    "path",
    [
        "dist/app.js",
        "frontend/build/index.html",
        "web/.next/server/app.js",
        "coverage/lcov-report/index.html",
    ],
)
def test_filters_generated_directories(path: str) -> None:
    result = FileFilter().filter((ChangedFile(path=Path(path), status=FileStatus.MODIFIED),))

    assert result.included_files == ()
    assert result.filtered_files[0].path == Path(path)
    assert result.filtered_files[0].reason is FilterReason.GENERATED_DIRECTORY


@pytest.mark.parametrize(
    "path",
    [
        "node_modules/package/index.js",
        "frontend/node_modules/package/index.js",
        "vendor/library/module.py",
        "src/vendor/library/module.py",
    ],
)
def test_filters_vendored_dependencies(path: str) -> None:
    result = FileFilter().filter((ChangedFile(path=Path(path), status=FileStatus.ADDED),))

    assert result.included_files == ()
    assert result.filtered_files[0].reason is FilterReason.VENDORED_DEPENDENCY


@pytest.mark.parametrize(
    "name",
    [
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "Cargo.lock",
        "Gemfile.lock",
        "composer.lock",
        "bun.lock",
        "bun.lockb",
    ],
)
def test_filters_lockfiles(name: str) -> None:
    result = FileFilter().filter(
        (ChangedFile(path=Path("workspace") / name, status=FileStatus.MODIFIED),)
    )

    assert result.included_files == ()
    assert result.filtered_files[0].reason is FilterReason.LOCKFILE


@pytest.mark.parametrize(
    "path",
    [
        "static/app.min.js",
        "static/styles.min.css",
        "static/worker.min.mjs",
        "static/config.min.cjs",
    ],
)
def test_filters_minified_assets(path: str) -> None:
    result = FileFilter().filter((ChangedFile(path=Path(path), status=FileStatus.MODIFIED),))

    assert result.included_files == ()
    assert result.filtered_files[0].reason is FilterReason.MINIFIED_ASSET


def test_preserves_meaningful_files_and_filtered_file_metadata() -> None:
    meaningful = ChangedFile(path=Path("src/build.py"), status=FileStatus.MODIFIED)
    similarly_named = ChangedFile(path=Path("src/vendor_tools.py"), status=FileStatus.ADDED)
    lockfile = ChangedFile(path=Path("package-lock.json"), status=FileStatus.DELETED)

    result = FileFilter().filter((meaningful, lockfile, similarly_named))

    assert result.included_files == (meaningful, similarly_named)
    assert result.filtered_files[0].path == lockfile.path
    assert result.filtered_files[0].status is FileStatus.DELETED
    assert result.filtered_files[0].reason is FilterReason.LOCKFILE


@pytest.mark.parametrize(
    ("old_path", "new_path"),
    [
        ("src/app.js", "dist/app.js"),
        ("vendor/app.js", "src/app.js"),
    ],
)
def test_keeps_renames_that_cross_the_filter_boundary(
    old_path: str,
    new_path: str,
) -> None:
    renamed = ChangedFile(
        path=Path(new_path),
        old_path=Path(old_path),
        status=FileStatus.RENAMED,
    )

    result = FileFilter().filter((renamed,))

    assert result.included_files == (renamed,)
    assert result.filtered_files == ()


def test_filters_rename_when_both_paths_are_low_signal() -> None:
    renamed = ChangedFile(
        path=Path("dist/app.js"),
        old_path=Path("build/app.js"),
        status=FileStatus.RENAMED,
    )

    result = FileFilter().filter((renamed,))

    assert result.included_files == ()
    assert result.filtered_files[0].reason is FilterReason.GENERATED_DIRECTORY


def test_default_patterns_can_be_replaced_for_future_project_configuration() -> None:
    changed_file = ChangedFile(path=Path("dist/app.js"), status=FileStatus.MODIFIED)

    result = FileFilter(generated_directories=()).filter((changed_file,))

    assert result.included_files == (changed_file,)
    assert result.filtered_files == ()


def test_filters_nested_git_ignore_style_configured_patterns() -> None:
    configured = ChangedFile(
        path=Path("packages/web/generated/client/api.py"),
        status=FileStatus.MODIFIED,
    )
    meaningful = ChangedFile(path=Path("packages/web/src/api.py"), status=FileStatus.MODIFIED)

    result = FileFilter(
        configured_exclusions=("packages/*/generated/**",),
    ).filter((configured, meaningful))

    assert result.included_files == (meaningful,)
    assert result.filtered_files[0].path == configured.path
    assert result.filtered_files[0].reason is FilterReason.CONFIGURED_EXCLUSION


def test_configured_exclusions_are_additive_to_built_in_defaults() -> None:
    configured = ChangedFile(path=Path("snapshots/output.txt"), status=FileStatus.MODIFIED)
    lockfile = ChangedFile(path=Path("package-lock.json"), status=FileStatus.MODIFIED)

    result = FileFilter(configured_exclusions=("snapshots/**",)).filter((configured, lockfile))

    assert tuple(item.reason for item in result.filtered_files) == (
        FilterReason.CONFIGURED_EXCLUSION,
        FilterReason.LOCKFILE,
    )


def test_built_in_reason_wins_when_configured_pattern_also_matches() -> None:
    changed_file = ChangedFile(path=Path("dist/app.min.js"), status=FileStatus.MODIFIED)

    result = FileFilter(configured_exclusions=("dist/**", "**/*.min.js")).filter((changed_file,))

    assert result.filtered_files[0].reason is FilterReason.GENERATED_DIRECTORY


def test_configured_filtering_preserves_input_order() -> None:
    first = ChangedFile(path=Path("tmp/z.py"), status=FileStatus.MODIFIED)
    second = ChangedFile(path=Path("tmp/a.py"), status=FileStatus.ADDED)

    result = FileFilter(configured_exclusions=("tmp/**",)).filter((first, second))

    assert tuple(item.path for item in result.filtered_files) == (first.path, second.path)


def test_configured_filter_keeps_rename_crossing_exclusion_boundary() -> None:
    renamed = ChangedFile(
        path=Path("archive/old.py"),
        old_path=Path("src/current.py"),
        status=FileStatus.RENAMED,
    )

    result = FileFilter(configured_exclusions=("archive/**",)).filter((renamed,))

    assert result.included_files == (renamed,)
    assert result.filtered_files == ()


def test_configured_filter_excludes_rename_when_both_paths_match() -> None:
    renamed = ChangedFile(
        path=Path("archive/new.py"),
        old_path=Path("archive/old.py"),
        status=FileStatus.RENAMED,
    )

    result = FileFilter(configured_exclusions=("archive/**",)).filter((renamed,))

    assert result.included_files == ()
    assert result.filtered_files[0].reason is FilterReason.CONFIGURED_EXCLUSION
