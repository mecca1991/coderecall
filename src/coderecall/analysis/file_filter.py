"""Exclude low-signal changed files from the main reasoning context."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from pathlib import Path

from coderecall.core.types import (
    ChangedFile,
    FileFilterResult,
    FilteredFile,
    FilterReason,
)

DEFAULT_GENERATED_DIRECTORIES = frozenset({"dist", "build", ".next", "coverage"})
DEFAULT_VENDORED_DIRECTORIES = frozenset({"node_modules", "vendor"})
DEFAULT_LOCKFILE_NAMES = frozenset(
    {
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
    }
)
DEFAULT_MINIFIED_SUFFIXES = (".min.js", ".min.css", ".min.mjs", ".min.cjs")


class FileFilter:
    """Classify changed files using replaceable default pattern sets."""

    def __init__(
        self,
        *,
        generated_directories: Collection[str] = DEFAULT_GENERATED_DIRECTORIES,
        vendored_directories: Collection[str] = DEFAULT_VENDORED_DIRECTORIES,
        lockfile_names: Collection[str] = DEFAULT_LOCKFILE_NAMES,
        minified_suffixes: Collection[str] = DEFAULT_MINIFIED_SUFFIXES,
    ) -> None:
        self.generated_directories = frozenset(generated_directories)
        self.vendored_directories = frozenset(vendored_directories)
        self.lockfile_names = frozenset(lockfile_names)
        self.minified_suffixes = tuple(suffix.lower() for suffix in minified_suffixes)

    def filter(self, changed_files: Iterable[ChangedFile]) -> FileFilterResult:
        """Separate meaningful files from low-signal files without discarding metadata."""

        included_files: list[ChangedFile] = []
        filtered_files: list[FilteredFile] = []

        for changed_file in changed_files:
            reason = self.reason_for(changed_file)
            if reason is None:
                included_files.append(changed_file)
                continue
            filtered_files.append(
                FilteredFile(
                    path=changed_file.path,
                    reason=reason,
                    status=changed_file.status,
                )
            )

        return FileFilterResult(
            included_files=tuple(included_files),
            filtered_files=tuple(filtered_files),
        )

    def reason_for(self, changed_file: ChangedFile) -> FilterReason | None:
        """Return a filter reason, preserving renames that cross a filter boundary."""

        destination_reason = self._reason_for_path(changed_file.path)
        if changed_file.old_path is None:
            return destination_reason

        source_reason = self._reason_for_path(changed_file.old_path)
        if destination_reason is not None and source_reason is not None:
            return destination_reason
        return None

    def _reason_for_path(self, path: Path) -> FilterReason | None:
        directory_parts = frozenset(path.parts[:-1])
        if directory_parts & self.generated_directories:
            return FilterReason.GENERATED_DIRECTORY
        if directory_parts & self.vendored_directories:
            return FilterReason.VENDORED_DEPENDENCY
        if path.name in self.lockfile_names:
            return FilterReason.LOCKFILE
        if path.name.lower().endswith(self.minified_suffixes):
            return FilterReason.MINIFIED_ASSET
        return None
