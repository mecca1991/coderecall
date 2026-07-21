"""Expected application errors with actionable user guidance."""

from __future__ import annotations

from pathlib import Path


class CodeRecallError(Exception):
    """Base class for failures CodeRecall can explain and recover from."""

    def __init__(
        self,
        message: str,
        *,
        recovery: str | None = None,
        debug_details: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.recovery = recovery
        self.debug_details = debug_details


class NotGitRepository(CodeRecallError):
    """Raised when the working directory is outside a Git repository."""


class DetachedHead(CodeRecallError):
    """Raised when Git cannot identify a current branch."""


class BaseBranchNotFound(CodeRecallError):
    """Raised when no valid comparison base can be selected."""


class DiffCollectionFailed(CodeRecallError):
    """Raised when Git diff data cannot be collected or parsed."""


class QuestionGenerationUnavailable(CodeRecallError):
    """Raised when a change lacks evidence for branch-specific questions."""


class DocumentationOnlyChanges(QuestionGenerationUnavailable):
    """Raised when documentation is the only meaningful branch change."""


class GitCommandFailed(CodeRecallError):
    """Raised when a Git command cannot be executed successfully."""


class ReportWriteFailed(CodeRecallError):
    """Raised when a local Markdown report cannot be written."""

    def __init__(self, target_path: Path, underlying_error: OSError) -> None:
        self.target_path = target_path
        self.underlying_error = underlying_error
        super().__init__(
            f'Could not write the local report to "{target_path}": {underlying_error}',
            recovery=(
                "Choose a writable location with `coderecall review --report <path>` and try again."
            ),
        )


class ProjectConfigError(CodeRecallError):
    """Raised when repository configuration cannot be read or validated."""


class ConfigInitializationFailed(CodeRecallError):
    """Raised when a starter project configuration cannot be created safely."""


class HookInstallationFailed(CodeRecallError):
    """Raised when a pre-push hook cannot be installed safely."""
