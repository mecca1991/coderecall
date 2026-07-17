"""Convert raw Git diff output into CodeRecall data types."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from coderecall.core.errors import DiffCollectionFailed
from coderecall.core.types import (
    ChangedFile,
    DiffCollection,
    DiffHunk,
    FileStatus,
    RepositoryContext,
)
from coderecall.git.git_adapter import GitAdapter

DEFAULT_MAX_PATCH_BYTES = 1_000_000

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)$")


class DiffCollector:
    """Collect changed files and unified patch hunks for a branch."""

    def __init__(
        self,
        git: GitAdapter,
        *,
        max_patch_bytes: int = DEFAULT_MAX_PATCH_BYTES,
    ) -> None:
        if max_patch_bytes < 1:
            raise ValueError("max_patch_bytes must be positive")
        self.git = git
        self.max_patch_bytes = max_patch_bytes

    def collect(
        self,
        repository: RepositoryContext,
        base_branch: str,
        *,
        include_uncommitted: bool = False,
    ) -> DiffCollection:
        """Collect one structured comparison against the selected base branch."""

        merge_base = self.git.find_merge_base(repository, base_branch)
        status_output = self.git.collect_name_status(
            repository,
            merge_base,
            include_uncommitted=include_uncommitted,
        )
        changed_files = self._parse_name_status(status_output)
        collected_files: list[ChangedFile] = []
        all_hunks: list[DiffHunk] = []
        uncertainty_notes: list[str] = []

        for changed_file in changed_files:
            paths = self._patch_paths(changed_file)
            patch = self.git.collect_patch(
                repository,
                merge_base,
                paths,
                include_uncommitted=include_uncommitted,
            )
            is_binary = self._is_binary_patch(patch)
            hunks: tuple[DiffHunk, ...] = ()

            if is_binary:
                uncertainty_notes.append(
                    f"Skipped patch hunks for binary file `{changed_file.path}`."
                )
            elif self._patch_size(patch) > self.max_patch_bytes:
                uncertainty_notes.append(
                    "Skipped patch hunks for "
                    f"`{changed_file.path}` because its patch exceeds "
                    f"{self.max_patch_bytes:,} bytes."
                )
            else:
                hunks = self._parse_hunks(changed_file.path, patch)
                all_hunks.extend(hunks)

            collected_files.append(replace(changed_file, is_binary=is_binary, hunks=hunks))

        return DiffCollection(
            merge_base=merge_base,
            changed_files=tuple(collected_files),
            diff_hunks=tuple(all_hunks),
            uncertainty_notes=tuple(uncertainty_notes),
            includes_uncommitted=include_uncommitted,
        )

    @staticmethod
    def _parse_name_status(output: str) -> tuple[ChangedFile, ...]:
        if not output:
            return ()

        fields = output.split("\0")
        if fields[-1] != "":
            raise DiffCollectionFailed(
                "Git returned malformed changed-file metadata.",
                debug_details="The NUL-delimited name-status output was not terminated.",
            )
        fields.pop()

        changed_files: list[ChangedFile] = []
        index = 0
        while index < len(fields):
            status_token = fields[index]
            index += 1
            if not status_token:
                raise DiffCollectionFailed(
                    "Git returned malformed changed-file metadata.",
                    debug_details="A changed file had an empty status.",
                )

            status_code = status_token[0]
            if status_code == "R":
                if index + 1 >= len(fields):
                    raise DiffCollectionFailed(
                        "Git returned malformed rename metadata.",
                        debug_details=f"Incomplete name-status record for `{status_token}`.",
                    )
                old_path = Path(fields[index])
                new_path = Path(fields[index + 1])
                index += 2
                changed_files.append(
                    ChangedFile(
                        path=new_path,
                        old_path=old_path,
                        status=FileStatus.RENAMED,
                    )
                )
                continue

            if index >= len(fields):
                raise DiffCollectionFailed(
                    "Git returned malformed changed-file metadata.",
                    debug_details=f"Missing path for status `{status_token}`.",
                )

            path = Path(fields[index])
            index += 1
            status = {
                "A": FileStatus.ADDED,
                "D": FileStatus.DELETED,
                "M": FileStatus.MODIFIED,
                "T": FileStatus.MODIFIED,
            }.get(status_code)
            if status is None:
                raise DiffCollectionFailed(
                    "Git returned an unsupported file status.",
                    debug_details=f"Status `{status_token}` for `{path}` is not supported.",
                )
            changed_files.append(ChangedFile(path=path, status=status))

        return tuple(changed_files)

    @staticmethod
    def _parse_hunks(file_path: Path, patch: str) -> tuple[DiffHunk, ...]:
        lines = patch.splitlines(keepends=True)
        hunk_starts = [index for index, line in enumerate(lines) if line.startswith("@@ ")]
        hunks: list[DiffHunk] = []

        for position, start in enumerate(hunk_starts):
            end = hunk_starts[position + 1] if position + 1 < len(hunk_starts) else len(lines)
            header = lines[start].rstrip("\r\n")
            match = _HUNK_HEADER.match(header)
            if match is None:
                raise DiffCollectionFailed(
                    "Git returned a patch hunk CodeRecall could not parse.",
                    debug_details=f"Invalid hunk header for `{file_path}`: {header}",
                )
            old_start, old_lines, new_start, new_lines = match.groups()
            hunks.append(
                DiffHunk(
                    file_path=file_path,
                    header=header,
                    old_start=int(old_start),
                    old_lines=int(old_lines) if old_lines is not None else 1,
                    new_start=int(new_start),
                    new_lines=int(new_lines) if new_lines is not None else 1,
                    patch="".join(lines[start:end]),
                )
            )

        return tuple(hunks)

    @staticmethod
    def _patch_paths(changed_file: ChangedFile) -> tuple[Path, ...]:
        if changed_file.old_path is None:
            return (changed_file.path,)
        return (changed_file.old_path, changed_file.path)

    @staticmethod
    def _is_binary_patch(patch: str) -> bool:
        return "GIT binary patch" in patch or any(
            line.startswith("Binary files ") for line in patch.splitlines()
        )

    @staticmethod
    def _patch_size(patch: str) -> int:
        return len(patch.encode("utf-8", errors="surrogateescape"))
