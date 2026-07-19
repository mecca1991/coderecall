"""Load and resolve repository-local CodeRecall configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pathspec import GitIgnoreSpec

from coderecall.core.errors import ProjectConfigError

CONFIG_FILENAME = ".coderecall.yml"
DEFAULT_REPORT_FILENAME = "coderecall-report.md"
DEFAULT_QUESTIONS = 3
DEFAULT_INCLUDE_UNCOMMITTED = False
STARTER_CONFIG = """base: main
report_path: coderecall-report.md
questions: 3
include_uncommitted: false
exclude:
  - node_modules/**
  - dist/**
  - build/**
  - vendor/**
"""
_KNOWN_KEYS = frozenset({"base", "report_path", "questions", "include_uncommitted", "exclude"})


@dataclass(frozen=True)
class ProjectConfig:
    """Validated optional values loaded from one repository config file."""

    base: str | None = None
    report_path: str | None = None
    questions: int | None = None
    include_uncommitted: bool | None = None
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class EffectiveReviewOptions:
    """Fully resolved options used to run one review."""

    base: str | None
    report_path: Path
    questions: int
    include_uncommitted: bool
    exclude: tuple[str, ...] = ()


class ConfigLoader:
    """Load the single configuration file at a detected repository root."""

    def load(self, repository_root: Path) -> ProjectConfig:
        """Return validated config, or no overrides when the file is absent or empty."""

        config_path = repository_root / CONFIG_FILENAME
        try:
            content = config_path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            if not config_path.is_symlink():
                return ProjectConfig()
            raise self._unreadable(config_path, error) from error
        except (OSError, UnicodeError) as error:
            raise self._unreadable(config_path, error) from error

        try:
            document = yaml.safe_load(content)
        except yaml.YAMLError as error:
            problem = str(error).splitlines()[0]
            raise ProjectConfigError(
                f'CodeRecall configuration at "{config_path}" is not valid YAML: {problem}',
                recovery="Fix the YAML syntax or unsafe tag and run CodeRecall again.",
            ) from error

        if document is None:
            return ProjectConfig()
        if not isinstance(document, dict):
            raise self._invalid(
                config_path,
                "the document must be a top-level mapping of configuration keys",
            )

        unknown_keys = sorted((key for key in document if key not in _KNOWN_KEYS), key=str)
        if unknown_keys:
            rendered = ", ".join(str(key) for key in unknown_keys)
            allowed = ", ".join(sorted(_KNOWN_KEYS))
            raise self._invalid(
                config_path,
                f"unknown configuration key(s): {rendered}",
                recovery=f"Use only these supported keys: {allowed}.",
            )

        return ProjectConfig(
            base=self._optional_non_empty_string(document, "base", config_path),
            report_path=self._optional_non_empty_string(document, "report_path", config_path),
            questions=self._questions(document, config_path),
            include_uncommitted=self._include_uncommitted(document, config_path),
            exclude=self._exclusions(document, config_path),
        )

    def _optional_non_empty_string(
        self,
        document: dict[Any, Any],
        key: str,
        config_path: Path,
    ) -> str | None:
        if key not in document:
            return None
        value = document[key]
        if not isinstance(value, str) or not value.strip():
            raise self._invalid(config_path, f"`{key}` must be a non-empty string")
        return value

    def _questions(self, document: dict[Any, Any], config_path: Path) -> int | None:
        if "questions" not in document:
            return None
        value = document["questions"]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3:
            raise self._invalid(config_path, "`questions` must be an integer from 1 to 3")
        return value

    def _include_uncommitted(
        self,
        document: dict[Any, Any],
        config_path: Path,
    ) -> bool | None:
        if "include_uncommitted" not in document:
            return None
        value = document["include_uncommitted"]
        if not isinstance(value, bool):
            raise self._invalid(config_path, "`include_uncommitted` must be true or false")
        return value

    def _exclusions(self, document: dict[Any, Any], config_path: Path) -> tuple[str, ...]:
        if "exclude" not in document:
            return ()
        value = document["exclude"]
        if not isinstance(value, list):
            raise self._invalid(config_path, "`exclude` must be a list of non-empty patterns")

        patterns: list[str] = []
        for index, pattern in enumerate(value):
            field = f"`exclude[{index}]`"
            if not isinstance(pattern, str) or not pattern.strip():
                raise self._invalid(config_path, f"{field} must be a non-empty string")
            if pattern.startswith("!"):
                raise self._invalid(
                    config_path,
                    f"{field} is negated; exclusions must be positive patterns",
                )
            if ".." in pattern.split("/"):
                raise self._invalid(
                    config_path,
                    f"{field} contains a traversal (`..`) path segment",
                )
            try:
                compiled = GitIgnoreSpec.from_lines((pattern,))
            except ValueError as error:
                raise self._invalid(
                    config_path,
                    f"{field} is not a valid Git-ignore-style pattern: {error}",
                ) from error
            if not compiled.patterns or compiled.patterns[0].include is not True:
                raise self._invalid(
                    config_path,
                    f"{field} must be a positive Git-ignore-style pattern",
                )
            patterns.append(pattern)
        return tuple(patterns)

    @staticmethod
    def _unreadable(config_path: Path, error: Exception) -> ProjectConfigError:
        return ProjectConfigError(
            f'Could not read CodeRecall configuration at "{config_path}": {error}',
            recovery="Make sure the config path is a readable UTF-8 file and try again.",
        )

    @staticmethod
    def _invalid(
        config_path: Path,
        problem: str,
        *,
        recovery: str | None = None,
    ) -> ProjectConfigError:
        return ProjectConfigError(
            f'Invalid CodeRecall configuration at "{config_path}": {problem}.',
            recovery=recovery or "Correct the configuration value and run CodeRecall again.",
        )


def resolve_review_options(
    *,
    config: ProjectConfig,
    repository_root: Path,
    invocation_directory: Path,
    base: str | None = None,
    report_path: Path | None = None,
    questions: int | None = None,
    include_uncommitted: bool | None = None,
) -> EffectiveReviewOptions:
    """Apply CLI-over-config precedence and anchor report paths to their source."""

    if report_path is not None:
        effective_report_path = _anchor(report_path, invocation_directory)
    elif config.report_path is not None:
        effective_report_path = _anchor(Path(config.report_path), repository_root)
    else:
        effective_report_path = invocation_directory / DEFAULT_REPORT_FILENAME

    return EffectiveReviewOptions(
        base=base if base is not None else config.base,
        report_path=effective_report_path,
        questions=questions if questions is not None else config.questions or DEFAULT_QUESTIONS,
        include_uncommitted=(
            include_uncommitted
            if include_uncommitted is not None
            else (
                config.include_uncommitted
                if config.include_uncommitted is not None
                else DEFAULT_INCLUDE_UNCOMMITTED
            )
        ),
        exclude=config.exclude,
    )


def _anchor(path: Path, directory: Path) -> Path:
    return path if path.is_absolute() else directory / path


__all__ = [
    "ConfigLoader",
    "EffectiveReviewOptions",
    "ProjectConfig",
    "resolve_review_options",
]
