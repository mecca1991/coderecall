"""Build a bounded model of the meaningful branch changes."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from coderecall.core.types import ChangeContext, ChangedFile, DiffCollection, RepositoryContext

_LANGUAGES_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
}
_TEST_DIRECTORIES = frozenset({"test", "tests", "__tests__"})


class ChangeModelBuilder:
    """Transform collected Git evidence into a change context."""

    def build(
        self,
        repository: RepositoryContext,
        base_branch: str,
        diff: DiffCollection,
    ) -> ChangeContext:
        """Preserve collected evidence in an immutable analysis context."""

        changed_files = tuple(
            self._classify_file(changed_file) for changed_file in diff.changed_files
        )
        related_tests = tuple(
            changed_file.path for changed_file in changed_files if changed_file.is_test
        )

        return ChangeContext(
            repo_root=repository.root,
            current_branch=repository.current_branch,
            base_branch=base_branch,
            merge_base=diff.merge_base,
            changed_files=changed_files,
            filtered_files=diff.filtered_files,
            diff_hunks=diff.diff_hunks,
            related_tests=related_tests,
            uncertainty_notes=diff.uncertainty_notes,
        )

    @staticmethod
    def _classify_file(changed_file: ChangedFile) -> ChangedFile:
        language = changed_file.language or _LANGUAGES_BY_SUFFIX.get(
            changed_file.path.suffix.lower()
        )
        is_test = changed_file.is_test or ChangeModelBuilder._is_test_path(changed_file.path)
        return replace(changed_file, language=language, is_test=is_test)

    @staticmethod
    def _is_test_path(path: Path) -> bool:
        if any(part.lower() in _TEST_DIRECTORIES for part in path.parts[:-1]):
            return True

        name = path.name.lower()
        stem = path.stem.lower()
        if path.suffix.lower() == ".py":
            return stem.startswith("test_") or stem.endswith("_test")
        return ".test." in name or ".spec." in name
