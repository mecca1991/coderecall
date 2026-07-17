"""Adapter for reading repository state through the Git executable."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, cast

from coderecall.core.errors import (
    BaseBranchNotFound,
    DetachedHead,
    DiffCollectionFailed,
    GitCommandFailed,
    NotGitRepository,
)
from coderecall.core.types import RepositoryContext

_PATCH_READ_SIZE = 64 * 1024
_PATCH_RECORD_HEADER = b"diff --git "
_DEFAULT_MAX_RAW_METADATA_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class RawPatchRecord:
    """One bounded file record from a Git patch stream."""

    content: bytes | None
    limit_reason: Literal["file", "aggregate"] | None = None

    @property
    def oversized(self) -> bool:
        return self.limit_reason is not None


@dataclass(frozen=True)
class RawDiffOutput:
    """Atomic raw metadata and bounded patch records from one Git process."""

    metadata: bytes
    patch_records: tuple[RawPatchRecord, ...]
    record_selection: tuple[bool, ...] | None = None


class _PrefixedReader:
    """Read an initial byte prefix before continuing from a binary stream."""

    def __init__(self, prefix: bytes, stream: BinaryIO) -> None:
        self.prefix = prefix
        self.stream = stream

    def readline(self, size: int) -> bytes:
        if not self.prefix:
            return self.stream.readline(size)

        newline = self.prefix.find(b"\n", 0, size)
        if newline >= 0:
            end = newline + 1
            result = self.prefix[:end]
            self.prefix = self.prefix[end:]
            return result

        result = self.prefix[:size]
        self.prefix = self.prefix[size:]
        if len(result) == size or self.prefix:
            return result

        remainder = self.stream.readline(size - len(result))
        return result + remainder


class GitAdapter:
    """Read Git repository metadata without exposing process details to callers."""

    def __init__(self, cwd: Path | None = None, *, executable: str = "git") -> None:
        self.cwd = cwd
        self.executable = executable

    def detect_repository(self) -> RepositoryContext:
        """Return the repository root and branch for the configured directory."""

        working_directory = (self.cwd or Path.cwd()).resolve()
        root_result = self._run("rev-parse", "--show-toplevel", cwd=working_directory)
        if root_result.returncode != 0:
            command = self._display_command("rev-parse", "--show-toplevel")
            details = root_result.stderr.strip() or f"Git exited with {root_result.returncode}."
            if "not a git repository" in details.lower():
                raise NotGitRepository(
                    f"CodeRecall could not find a Git repository from {working_directory}.",
                    recovery="Run this command inside a Git working tree.",
                    debug_details=f"{command}: {details}",
                )
            raise GitCommandFailed(
                "CodeRecall could not inspect the current Git repository.",
                recovery="Resolve the Git error and run CodeRecall again.",
                debug_details=f"{command}: {details}",
            )

        root_text = root_result.stdout.strip()
        if not root_text:
            raise GitCommandFailed(
                "Git did not return a repository root.",
                recovery="Check the repository and run CodeRecall again.",
                debug_details=self._display_command("rev-parse", "--show-toplevel"),
            )

        root = Path(root_text).resolve()
        branch_result = self._run("branch", "--show-current", cwd=root)
        self._require_success(branch_result, "branch", "--show-current")

        branch = branch_result.stdout.strip()
        if not branch:
            raise DetachedHead(
                "CodeRecall cannot start a review while HEAD is detached.",
                recovery="Check out the branch you want to review and run CodeRecall again.",
                debug_details=self._display_command("branch", "--show-current"),
            )

        return RepositoryContext(root=root, current_branch=branch)

    def select_base_branch(
        self,
        repository: RepositoryContext,
        requested_base: str | None = None,
    ) -> str:
        """Validate an explicit base or infer a conventional local base branch."""

        if requested_base is not None:
            if not requested_base:
                raise BaseBranchNotFound(
                    "Base branch cannot be empty.",
                    recovery="Provide an existing branch with `coderecall review --base <branch>`.",
                )
            if self._ref_exists(repository.root, requested_base):
                return requested_base
            raise BaseBranchNotFound(
                f"Could not find base branch `{requested_base}`.",
                recovery=(
                    "Choose an existing branch with "
                    "`coderecall review --base <branch>` or update your local refs."
                ),
                debug_details=self._display_command(
                    "rev-parse",
                    "--verify",
                    "--end-of-options",
                    f"{requested_base}^{{commit}}",
                ),
            )

        for candidate in ("main", "master"):
            if self._ref_exists(repository.root, candidate):
                return candidate

        raise BaseBranchNotFound(
            "CodeRecall could not infer a base branch (`main` or `master`).",
            recovery="Run `coderecall review --base <branch>` with an existing branch.",
            debug_details="Checked local refs: main, master.",
        )

    def find_merge_base(self, repository: RepositoryContext, base_branch: str) -> str:
        """Return the common ancestor used for the branch comparison."""

        result = self._run("merge-base", base_branch, "HEAD", cwd=repository.root)
        if result.returncode == 1 and not result.stdout.strip():
            self._raise_missing_merge_base(base_branch)
        if result.returncode != 0:
            self._raise_diff_failure(result, "merge-base", base_branch, "HEAD")

        merge_bases = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not merge_bases:
            self._raise_missing_merge_base(base_branch)
        return merge_bases[0]

    def collect_diff(
        self,
        repository: RepositoryContext,
        merge_base: str,
        *,
        max_patch_bytes: int,
        max_total_patch_bytes: int,
        max_changed_files: int,
        max_raw_metadata_bytes: int = _DEFAULT_MAX_RAW_METADATA_BYTES,
        record_selector: Callable[[bytes], tuple[bool, ...]] | None = None,
        include_uncommitted: bool = False,
    ) -> RawDiffOutput:
        """Stream atomic file metadata and bounded patches in diff order."""

        revisions = self._diff_revisions(merge_base, include_uncommitted)
        arguments = (
            "diff",
            "--raw",
            "-z",
            "--patch",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--find-renames",
            "--submodule=short",
            "--unified=80",
            *revisions,
            "--",
        )
        command = [self.executable, *arguments]
        environment = self._git_environment()

        try:
            with tempfile.TemporaryFile() as stderr_file:
                process = subprocess.Popen(
                    command,
                    cwd=repository.root,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=stderr_file,
                )
                if process.stdout is None:
                    process.kill()
                    process.wait()
                    raise DiffCollectionFailed(
                        "CodeRecall could not read the Git patch stream.",
                        debug_details=self._display_command(*arguments),
                    )
                try:
                    output = self._read_diff_output(
                        cast(BinaryIO, process.stdout),
                        max_patch_bytes=max_patch_bytes,
                        max_total_patch_bytes=max_total_patch_bytes,
                        max_changed_files=max_changed_files,
                        max_raw_metadata_bytes=max_raw_metadata_bytes,
                        record_selector=record_selector,
                    )
                except BaseException:
                    process.kill()
                    process.wait()
                    raise
                finally:
                    process.stdout.close()

                return_code = process.wait()
                stderr_file.seek(0)
                stderr = stderr_file.read().decode("utf-8", errors="surrogateescape")
        except FileNotFoundError as error:
            raise GitCommandFailed(
                "CodeRecall could not find the Git executable.",
                recovery="Install Git and ensure it is available on PATH.",
                debug_details=self._display_command(*arguments),
            ) from error
        except OSError as error:
            raise GitCommandFailed(
                "CodeRecall could not run Git.",
                recovery="Check the Git installation and repository permissions.",
                debug_details=f"{self._display_command(*arguments)}: {error}",
            ) from error

        if return_code != 0:
            result = subprocess.CompletedProcess(command, return_code, "", stderr)
            self._raise_diff_failure(result, *arguments)
        return output

    def _ref_exists(self, root: Path, reference: str) -> bool:
        result = self._run(
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            f"{reference}^{{commit}}",
            cwd=root,
        )
        return result.returncode == 0

    def _run(
        self,
        *arguments: str,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        command = [self.executable, *arguments]
        try:
            return subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                check=False,
                encoding="utf-8",
                env=self._git_environment(),
                errors="surrogateescape",
            )
        except FileNotFoundError as error:
            raise GitCommandFailed(
                "CodeRecall could not find the Git executable.",
                recovery="Install Git and ensure it is available on PATH.",
                debug_details=self._display_command(*arguments),
            ) from error
        except OSError as error:
            raise GitCommandFailed(
                "CodeRecall could not run Git.",
                recovery="Check the Git installation and repository permissions.",
                debug_details=f"{self._display_command(*arguments)}: {error}",
            ) from error

    def _require_success(
        self,
        result: subprocess.CompletedProcess[str],
        *arguments: str,
    ) -> None:
        if result.returncode == 0:
            return

        details = result.stderr.strip() or f"Git exited with {result.returncode}."
        raise GitCommandFailed(
            "CodeRecall could not read the current Git branch.",
            recovery="Resolve the Git error and run CodeRecall again.",
            debug_details=f"{self._display_command(*arguments)}: {details}",
        )

    def _display_command(self, *arguments: str) -> str:
        return " ".join((self.executable, *arguments))

    @staticmethod
    def _git_environment() -> dict[str, str]:
        environment = os.environ.copy()
        environment.update({"LANG": "C", "LC_ALL": "C"})
        environment.pop("LANGUAGE", None)
        return environment

    @staticmethod
    def _diff_revisions(merge_base: str, include_uncommitted: bool) -> tuple[str, ...]:
        if include_uncommitted:
            return (merge_base,)
        return (merge_base, "HEAD")

    def _raise_diff_failure(
        self,
        result: subprocess.CompletedProcess[str],
        *arguments: str,
    ) -> None:
        details = result.stderr.strip() or f"Git exited with {result.returncode}."
        raise DiffCollectionFailed(
            "CodeRecall could not collect the Git diff.",
            recovery="Resolve the Git error and run CodeRecall again.",
            debug_details=f"{self._display_command(*arguments)}: {details}",
        )

    def _raise_missing_merge_base(self, base_branch: str) -> None:
        raise DiffCollectionFailed(
            f"Git could not find a merge base between `{base_branch}` and `HEAD`.",
            recovery="Choose a base branch that shares history with the current branch.",
            debug_details=self._display_command("merge-base", base_branch, "HEAD"),
        )

    @staticmethod
    def _read_diff_output(
        stdout: BinaryIO,
        *,
        max_patch_bytes: int,
        max_total_patch_bytes: int,
        max_changed_files: int,
        max_raw_metadata_bytes: int,
        record_selector: Callable[[bytes], tuple[bool, ...]] | None,
    ) -> RawDiffOutput:
        metadata = bytearray()
        trailing_null = b""

        while True:
            chunk = stdout.read(_PATCH_READ_SIZE)
            if not chunk:
                if metadata or trailing_null:
                    raise DiffCollectionFailed(
                        "Git returned incomplete raw diff metadata.",
                        recovery="Run the command again after checking the repository state.",
                    )
                selection = record_selector(b"") if record_selector is not None else None
                return RawDiffOutput(
                    metadata=b"",
                    patch_records=(),
                    record_selection=selection,
                )

            combined = trailing_null + chunk
            separator = combined.find(b"\0\0")
            if separator >= 0:
                metadata.extend(combined[:separator])
                patch_prefix = combined[separator + 2 :]
                if len(metadata) > max_raw_metadata_bytes:
                    GitAdapter._raise_raw_metadata_limit(max_raw_metadata_bytes)
                break

            if combined.endswith(b"\0"):
                metadata.extend(combined[:-1])
                trailing_null = b"\0"
            else:
                metadata.extend(combined)
                trailing_null = b""

            if len(metadata) > max_raw_metadata_bytes:
                GitAdapter._raise_raw_metadata_limit(max_raw_metadata_bytes)

        metadata_bytes = bytes(metadata)
        selection = record_selector(metadata_bytes) if record_selector is not None else None
        if selection is not None and sum(selection) > max_changed_files:
            raise DiffCollectionFailed(
                f"Git diff contains more than {max_changed_files:,} files for analysis.",
                recovery="Review a smaller change or raise the configured analysis file limit.",
            )

        patch_reader = _PrefixedReader(patch_prefix, stdout)
        patch_records = GitAdapter._read_patch_records(
            patch_reader,
            max_patch_bytes=max_patch_bytes,
            max_total_patch_bytes=max_total_patch_bytes,
            max_changed_files=max_changed_files,
            record_selection=selection,
        )
        return RawDiffOutput(
            metadata=metadata_bytes,
            patch_records=patch_records,
            record_selection=selection,
        )

    @staticmethod
    def _raise_raw_metadata_limit(max_raw_metadata_bytes: int) -> None:
        raise DiffCollectionFailed(
            "Git diff metadata exceeds CodeRecall's raw metadata safety limit.",
            recovery=(
                "Review a smaller change or raise the configured raw metadata limit "
                f"above {max_raw_metadata_bytes:,} bytes."
            ),
        )

    @staticmethod
    def _read_patch_records(
        stdout: _PrefixedReader,
        *,
        max_patch_bytes: int,
        max_total_patch_bytes: int,
        max_changed_files: int,
        record_selection: tuple[bool, ...] | None,
    ) -> tuple[RawPatchRecord, ...]:
        records: list[RawPatchRecord] = []
        current_parts: list[bytes] = []
        current_size = 0
        current_limit_reason: Literal["file", "aggregate"] | None = None
        total_buffered_bytes = 0
        in_record = False
        include_current_record = False
        raw_record_count = 0
        at_line_start = True

        def finish_record() -> None:
            nonlocal total_buffered_bytes
            content = None if current_limit_reason is not None else b"".join(current_parts)
            records.append(RawPatchRecord(content=content, limit_reason=current_limit_reason))
            if content is not None:
                total_buffered_bytes += len(content)

        while True:
            fragment = stdout.readline(_PATCH_READ_SIZE)
            if not fragment:
                break
            starts_record = at_line_start and fragment.startswith(_PATCH_RECORD_HEADER)
            if starts_record:
                if in_record and include_current_record:
                    finish_record()
                if record_selection is not None and raw_record_count >= len(record_selection):
                    raise DiffCollectionFailed(
                        "Git returned more patch records than changed-file metadata.",
                        recovery="Run the command again after checking the repository state.",
                    )
                include_current_record = (
                    record_selection is None or record_selection[raw_record_count]
                )
                raw_record_count += 1
                if include_current_record and len(records) >= max_changed_files:
                    raise DiffCollectionFailed(
                        f"Git diff contains more than {max_changed_files:,} changed files.",
                        recovery="Review a smaller change or raise the configured file limit.",
                    )
                in_record = True
                current_parts = []
                current_size = 0
                current_limit_reason = None

            if in_record and include_current_record and current_limit_reason is None:
                current_size += len(fragment)
                if current_size > max_patch_bytes:
                    current_parts.clear()
                    current_limit_reason = "file"
                elif total_buffered_bytes + current_size > max_total_patch_bytes:
                    current_parts.clear()
                    current_limit_reason = "aggregate"
                else:
                    current_parts.append(fragment)

            at_line_start = fragment.endswith(b"\n")

        if in_record and include_current_record:
            finish_record()
        if record_selection is not None and raw_record_count != len(record_selection):
            raise DiffCollectionFailed(
                "Git returned fewer patch records than changed-file metadata.",
                recovery="Run the command again after checking the repository state.",
            )
        return tuple(records)
