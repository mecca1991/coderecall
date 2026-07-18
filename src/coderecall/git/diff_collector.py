"""Convert raw Git diff output into CodeRecall data types."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from coderecall.core.errors import DiffCollectionFailed
from coderecall.core.types import (
    ChangedFile,
    DiffCollection,
    DiffHunk,
    FileFilterResult,
    FileStatus,
    FilteredFile,
    RepositoryContext,
)
from coderecall.git.git_adapter import GitAdapter

DEFAULT_MAX_PATCH_BYTES = 1_000_000
DEFAULT_MAX_TOTAL_PATCH_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_CHANGED_FILES = 1_000
DEFAULT_MAX_RAW_METADATA_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_RAW_CHANGED_FILES = 10_000

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)$")
_BINARY_MARKER = re.compile(r"(?m)^(?:GIT binary patch|Binary files .+ differ)\r?$")


class _ChangedFileFilter(Protocol):
    def filter(self, changed_files: Iterable[ChangedFile]) -> FileFilterResult:
        """Separate meaningful files from filtered records."""


class DiffCollector:
    """Collect changed files and unified patch hunks for a branch."""

    def __init__(
        self,
        git: GitAdapter,
        *,
        max_patch_bytes: int = DEFAULT_MAX_PATCH_BYTES,
        max_total_patch_bytes: int = DEFAULT_MAX_TOTAL_PATCH_BYTES,
        max_changed_files: int = DEFAULT_MAX_CHANGED_FILES,
        max_raw_metadata_bytes: int = DEFAULT_MAX_RAW_METADATA_BYTES,
        max_raw_changed_files: int = DEFAULT_MAX_RAW_CHANGED_FILES,
        file_filter: _ChangedFileFilter | None = None,
    ) -> None:
        if max_patch_bytes < 1:
            raise ValueError("max_patch_bytes must be positive")
        if max_total_patch_bytes < 1:
            raise ValueError("max_total_patch_bytes must be positive")
        if max_changed_files < 1:
            raise ValueError("max_changed_files must be positive")
        if max_raw_metadata_bytes < 1:
            raise ValueError("max_raw_metadata_bytes must be positive")
        if max_raw_changed_files < 1:
            raise ValueError("max_raw_changed_files must be positive")
        self.git = git
        self.max_patch_bytes = max_patch_bytes
        self.max_total_patch_bytes = max_total_patch_bytes
        self.max_changed_files = max_changed_files
        self.max_raw_metadata_bytes = max_raw_metadata_bytes
        self.max_raw_changed_files = max_raw_changed_files
        self.file_filter = file_filter

    def collect(
        self,
        repository: RepositoryContext,
        base_branch: str,
        *,
        include_uncommitted: bool = False,
    ) -> DiffCollection:
        """Collect one structured comparison against the selected base branch."""

        merge_base = self.git.find_merge_base(repository, base_branch)
        source_revision = self.git.resolve_revision(repository, "HEAD")
        filter_result: FileFilterResult | None = None

        def select_records(metadata: bytes) -> tuple[bool, ...]:
            nonlocal filter_result
            raw_changed_files = self._parse_raw_metadata(
                metadata,
                max_changed_files=self.max_raw_changed_files,
            )
            if self.file_filter is None:
                return (True,) * len(raw_changed_files)
            filter_result = self.file_filter.filter(raw_changed_files)
            included_files = frozenset(filter_result.included_files)
            return tuple(changed_file in included_files for changed_file in raw_changed_files)

        raw_diff = self.git.collect_diff(
            repository,
            merge_base,
            max_patch_bytes=self.max_patch_bytes,
            max_total_patch_bytes=self.max_total_patch_bytes,
            max_changed_files=self.max_changed_files,
            max_raw_metadata_bytes=self.max_raw_metadata_bytes,
            record_selector=select_records if self.file_filter is not None else None,
            include_uncommitted=include_uncommitted,
            target_revision=source_revision,
        )
        if filter_result is None:
            changed_files = self._parse_raw_metadata(
                raw_diff.metadata,
                max_changed_files=self.max_raw_changed_files,
            )
            filtered_files: tuple[FilteredFile, ...] = ()
        else:
            changed_files = filter_result.included_files
            filtered_files = filter_result.filtered_files
        patch_records = raw_diff.patch_records
        if len(patch_records) != len(changed_files):
            raise DiffCollectionFailed(
                "Git returned inconsistent changed-file and patch metadata.",
                recovery="Run the command again after checking the repository state.",
                debug_details=(
                    f"Received {len(changed_files)} file records and "
                    f"{len(patch_records)} patch records."
                ),
            )

        collected_files: list[ChangedFile] = []
        all_hunks: list[DiffHunk] = []
        uncertainty_notes: list[str] = []
        aggregate_omissions = 0

        for changed_file, patch_record in zip(changed_files, patch_records, strict=True):
            is_binary = False
            hunks: tuple[DiffHunk, ...] = ()

            if patch_record.limit_reason == "file":
                uncertainty_notes.append(
                    "Skipped patch hunks for "
                    f"{self._format_path(changed_file.path)} because its patch exceeds "
                    f"{self.max_patch_bytes:,} bytes."
                )
            elif patch_record.limit_reason == "aggregate":
                aggregate_omissions += 1
            else:
                patch = (patch_record.content or b"").decode("utf-8", errors="surrogateescape")
                is_binary = self._is_binary_patch(patch)
                if is_binary:
                    uncertainty_notes.append(
                        "Skipped patch hunks for binary file "
                        f"{self._format_path(changed_file.path)}."
                    )
                else:
                    hunks = self._parse_hunks(changed_file.path, patch)
                    all_hunks.extend(hunks)

            collected_files.append(replace(changed_file, is_binary=is_binary, hunks=hunks))

        if aggregate_omissions:
            uncertainty_notes.append(
                f"Skipped patch hunks for {aggregate_omissions:,} files because buffered "
                f"patch data exceeds {self.max_total_patch_bytes:,} bytes."
            )

        return DiffCollection(
            merge_base=merge_base,
            changed_files=tuple(collected_files),
            filtered_files=filtered_files,
            diff_hunks=tuple(all_hunks),
            uncertainty_notes=tuple(uncertainty_notes),
            includes_uncommitted=include_uncommitted,
            source_revision=source_revision,
        )

    @staticmethod
    def _parse_raw_metadata(
        metadata: bytes,
        *,
        max_changed_files: int | None = None,
    ) -> tuple[ChangedFile, ...]:
        if not metadata:
            return ()

        fields = metadata.split(b"\0")
        changed_files: list[ChangedFile] = []
        index = 0
        while index < len(fields):
            header = fields[index]
            index += 1
            if not header.startswith(b":") or b" " not in header:
                raise DiffCollectionFailed(
                    "Git returned malformed changed-file metadata.",
                    debug_details="A raw diff record had an invalid header.",
                )

            status_bytes = header.rsplit(b" ", 1)[-1]
            try:
                status_token = status_bytes.decode("ascii")
            except UnicodeDecodeError as error:
                raise DiffCollectionFailed(
                    "Git returned malformed changed-file metadata.",
                    debug_details="A raw diff record had a non-ASCII status.",
                ) from error
            if not status_token:
                raise DiffCollectionFailed(
                    "Git returned malformed changed-file metadata.",
                    debug_details="A raw diff record had an empty status.",
                )
            status_code = status_token[0]
            if status_code == "R":
                if index + 1 >= len(fields):
                    raise DiffCollectionFailed(
                        "Git returned malformed rename metadata.",
                        debug_details=f"Incomplete name-status record for `{status_token}`.",
                    )
                old_path = DiffCollector._decode_path(fields[index])
                new_path = DiffCollector._decode_path(fields[index + 1])
                index += 2
                changed_files.append(
                    ChangedFile(
                        path=new_path,
                        old_path=old_path,
                        status=FileStatus.RENAMED,
                    )
                )
                DiffCollector._enforce_raw_file_limit(changed_files, max_changed_files)
                continue

            if index >= len(fields):
                raise DiffCollectionFailed(
                    "Git returned malformed changed-file metadata.",
                    debug_details=f"Missing path for status `{status_token}`.",
                )

            path = DiffCollector._decode_path(fields[index])
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
            DiffCollector._enforce_raw_file_limit(changed_files, max_changed_files)

        return tuple(changed_files)

    @staticmethod
    def _enforce_raw_file_limit(
        changed_files: list[ChangedFile],
        max_changed_files: int | None,
    ) -> None:
        if max_changed_files is None or len(changed_files) <= max_changed_files:
            return
        raise DiffCollectionFailed(
            f"Git diff contains more than {max_changed_files:,} raw changed files.",
            recovery="Review a smaller change or raise the configured raw changed-file limit.",
        )

    @staticmethod
    def _parse_hunks(file_path: Path, patch: str) -> tuple[DiffHunk, ...]:
        lines = DiffCollector._split_lf_lines(patch)
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
    def _is_binary_patch(patch: str) -> bool:
        return _BINARY_MARKER.search(patch) is not None

    @staticmethod
    def _format_path(path: Path) -> str:
        return json.dumps(str(path), ensure_ascii=True)

    @staticmethod
    def _decode_path(value: bytes) -> Path:
        return Path(value.decode("utf-8", errors="surrogateescape"))

    @staticmethod
    def _split_lf_lines(value: str) -> list[str]:
        parts = value.split("\n")
        lines = [f"{part}\n" for part in parts[:-1]]
        if parts[-1]:
            lines.append(parts[-1])
        return lines
