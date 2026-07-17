"""Adapter for reading repository state through the Git executable."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from coderecall.core.errors import (
    BaseBranchNotFound,
    DetachedHead,
    GitCommandFailed,
    NotGitRepository,
)
from coderecall.core.types import RepositoryContext


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
        environment = os.environ.copy()
        environment.update({"LANG": "C", "LC_ALL": "C"})
        environment.pop("LANGUAGE", None)
        try:
            return subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                check=False,
                env=environment,
                text=True,
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
