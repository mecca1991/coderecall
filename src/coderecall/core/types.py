"""Core data types shared across CodeRecall services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class FileStatus(StrEnum):
    """Git status for a changed file."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


class FilterReason(StrEnum):
    """Why a changed file was excluded from the reasoning context."""

    GENERATED_DIRECTORY = "generated directory"
    VENDORED_DEPENDENCY = "vendored dependency"
    LOCKFILE = "lockfile"
    MINIFIED_ASSET = "minified asset"


class QuestionCategory(StrEnum):
    """Default learning-question categories."""

    BEHAVIOR = "behavior"
    FAILURE = "failure"
    EVIDENCE = "evidence"
    FOLLOW_UP = "follow_up"


class AssessmentLabel(StrEnum):
    """Non-numeric answer assessment labels."""

    STRONG = "Strong"
    PARTIAL = "Partial"
    GAP_FOUND = "Gap found"
    UNCERTAIN = "Uncertain"


@dataclass(frozen=True)
class RepositoryContext:
    """The Git repository containing the current CodeRecall invocation."""

    root: Path
    current_branch: str


@dataclass(frozen=True)
class DiffHunk:
    """One patch hunk from a changed file."""

    file_path: Path
    header: str
    old_start: int | None = None
    old_lines: int | None = None
    new_start: int | None = None
    new_lines: int | None = None
    patch: str = ""


@dataclass(frozen=True)
class ChangedFile:
    """One changed file in the branch diff."""

    path: Path
    status: FileStatus
    old_path: Path | None = None
    language: str | None = None
    is_binary: bool = False
    is_test: bool = False
    hunks: tuple[DiffHunk, ...] = ()


@dataclass(frozen=True)
class DiffCollection:
    """Structured Git changes collected for one review run."""

    merge_base: str
    changed_files: tuple[ChangedFile, ...] = ()
    diff_hunks: tuple[DiffHunk, ...] = ()
    uncertainty_notes: tuple[str, ...] = ()
    includes_uncommitted: bool = False


@dataclass(frozen=True)
class FilteredFile:
    """A file excluded from the main reasoning context."""

    path: Path
    reason: FilterReason
    status: FileStatus | None = None


@dataclass(frozen=True)
class FileFilterResult:
    """Meaningful and filtered files produced by one filtering pass."""

    included_files: tuple[ChangedFile, ...] = ()
    filtered_files: tuple[FilteredFile, ...] = ()


@dataclass(frozen=True)
class EvidenceCitation:
    """Local repository evidence used by a question or assessment."""

    kind: str
    file_path: Path
    symbol: str | None = None
    hunk_header: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    note: str | None = None


@dataclass(frozen=True)
class ChangeContext:
    """The repository change being reviewed."""

    repo_root: Path
    current_branch: str
    base_branch: str
    merge_base: str | None = None
    changed_files: tuple[ChangedFile, ...] = ()
    filtered_files: tuple[FilteredFile, ...] = ()
    diff_hunks: tuple[DiffHunk, ...] = ()
    changed_symbols: tuple[str, ...] = ()
    related_tests: tuple[Path, ...] = ()
    likely_side_effects: tuple[str, ...] = ()
    uncertainty_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Question:
    """A generated learning prompt."""

    id: str
    category: QuestionCategory
    prompt: str
    rationale: str
    references: tuple[EvidenceCitation, ...] = ()


@dataclass(frozen=True)
class Answer:
    """A developer's response to a question."""

    question_id: str
    raw_text: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    skipped: bool = False


@dataclass(frozen=True)
class Assessment:
    """Evidence-grounded feedback for an answer."""

    question_id: str
    label: AssessmentLabel
    summary: str
    confidence: str
    strengths: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    evidence: tuple[EvidenceCitation, ...] = ()
    uncertainty_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FollowUp:
    """One optional adaptive follow-up prompt and response."""

    question: Question
    answer: Answer | None = None
    assessment: Assessment | None = None


@dataclass(frozen=True)
class Report:
    """The final local report payload."""

    session_metadata: dict[str, str]
    diff_summary: str
    questions: tuple[Question, ...]
    answers: tuple[Answer, ...]
    assessments: tuple[Assessment, ...]
    follow_up: FollowUp | None = None
    review_talking_points: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewSession:
    """One completed or partially completed review run."""

    metadata: dict[str, str]
    change_context: ChangeContext
    summary: str
    questions: tuple[Question, ...] = ()
    answers: tuple[Answer, ...] = ()
    assessments: tuple[Assessment, ...] = ()
    follow_up: FollowUp | None = None
    report_path: Path | None = None
