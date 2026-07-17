"""Expected application errors with actionable user guidance."""

from __future__ import annotations


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


class GitCommandFailed(CodeRecallError):
    """Raised when a Git command cannot be executed successfully."""
