"""Adapter for reading repository state through the Git executable."""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

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


@dataclass(frozen=True)
class RawPatchRecord:
    """One bounded file record from a Git patch stream."""

    content: bytes | None
    oversized: bool = False


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

    def collect_name_status(
        self,
        repository: RepositoryContext,
        merge_base: str,
        *,
        include_uncommitted: bool = False,
    ) -> str:
        """Return NUL-delimited changed-file metadata from Git."""

        revisions = self._diff_revisions(merge_base, include_uncommitted)
        arguments = (
            "diff",
            "--name-status",
            "-z",
            "--find-renames",
            "--no-ext-diff",
            *revisions,
            "--",
        )
        result = self._run(*arguments, cwd=repository.root)
        if result.returncode != 0:
            self._raise_diff_failure(result, *arguments)
        return result.stdout

    def collect_patch_records(
        self,
        repository: RepositoryContext,
        merge_base: str,
        *,
        max_patch_bytes: int,
        include_uncommitted: bool = False,
    ) -> tuple[RawPatchRecord, ...]:
        """Stream bounded patch records for all changed files in diff order."""

        revisions = self._diff_revisions(merge_base, include_uncommitted)
        arguments = (
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--find-renames",
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
                    records = self._read_patch_records(
                        cast(BinaryIO, process.stdout), max_patch_bytes
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
        return records

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
    def _read_patch_records(
        stdout: BinaryIO,
        max_patch_bytes: int,
    ) -> tuple[RawPatchRecord, ...]:
        records: list[RawPatchRecord] = []
        current_parts: list[bytes] = []
        current_size = 0
        current_oversized = False
        in_record = False
        at_line_start = True

        while True:
            fragment = stdout.readline(_PATCH_READ_SIZE)
            if not fragment:
                break
            starts_record = at_line_start and fragment.startswith(_PATCH_RECORD_HEADER)
            if starts_record:
                if in_record:
                    records.append(
                        RawPatchRecord(
                            content=None if current_oversized else b"".join(current_parts),
                            oversized=current_oversized,
                        )
                    )
                in_record = True
                current_parts = []
                current_size = 0
                current_oversized = False

            if in_record and not current_oversized:
                current_size += len(fragment)
                if current_size > max_patch_bytes:
                    current_parts.clear()
                    current_oversized = True
                else:
                    current_parts.append(fragment)

            at_line_start = fragment.endswith(b"\n")

        if in_record:
            records.append(
                RawPatchRecord(
                    content=None if current_oversized else b"".join(current_parts),
                    oversized=current_oversized,
                )
            )
        return tuple(records)
