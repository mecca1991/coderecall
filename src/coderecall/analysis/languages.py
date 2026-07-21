"""Recognize changed-file languages and describe symbol-analysis coverage."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from coderecall.core.types import ChangedFile

UNSUPPORTED_LANGUAGE_NOTE_PREFIX = "Symbol-level analysis was unavailable for "
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
    ".dart": "dart",
    ".go": "go",
    ".md": "markdown",
    ".markdown": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
}
SYMBOL_EXTRACTOR_LANGUAGES = frozenset({"python", "javascript", "typescript"})
_DISPLAY_NAMES = {
    "python": "Python",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "dart": "Dart",
    "go": "Go",
    "markdown": "Markdown",
    "yaml": "YAML",
    "json": "JSON",
}


def recognize_language(path: Path, declared_language: str | None = None) -> str | None:
    """Return a declared or suffix-recognized language identifier."""

    return declared_language or _LANGUAGES_BY_SUFFIX.get(path.suffix.lower())


def has_symbol_extractor(changed_file: ChangedFile) -> bool:
    """Return whether a changed file has a supported language extractor."""

    language = recognize_language(changed_file.path, changed_file.language)
    return language in SYMBOL_EXTRACTOR_LANGUAGES


def unsupported_language_labels(changed_files: Iterable[ChangedFile]) -> tuple[str, ...]:
    """Describe unsupported non-binary source types in first-seen order."""

    labels_by_type: dict[str, str] = {}
    for changed_file in changed_files:
        if changed_file.is_binary or has_symbol_extractor(changed_file):
            continue

        suffix = changed_file.path.suffix.lower()
        language = recognize_language(changed_file.path, changed_file.language)
        type_key = language or suffix or "unrecognized"
        if type_key in labels_by_type:
            continue

        display_name = _DISPLAY_NAMES.get(language or "")
        if display_name is not None and suffix:
            label = f"{display_name} ({_escape_value(suffix)})"
        elif display_name is not None:
            label = display_name
        elif suffix:
            label = _escape_value(suffix)
        else:
            label = "unrecognized"
        labels_by_type[type_key] = label
    return tuple(labels_by_type.values())


def format_language_list(labels: tuple[str, ...]) -> str:
    """Join language labels into a compact natural-language list."""

    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return " and ".join(labels)
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def unsupported_language_note(labels: tuple[str, ...]) -> str:
    """Build the shared symbol-analysis limit disclosure."""

    return (
        f"{UNSUPPORTED_LANGUAGE_NOTE_PREFIX}{format_language_list(labels)}; any symbols "
        "inferred from hunk context are heuristic."
    )


def _escape_value(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)[1:-1]
