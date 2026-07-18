"""Detect likely side effects from bounded change evidence."""

from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import replace
from pathlib import Path

from coderecall.core.types import (
    ChangeContext,
    CodeReference,
    DiffHunk,
    EvidenceCitation,
    LikelySideEffect,
    SideEffectKind,
)

_TRANSACTION_CALLS = frozenset({"atomic", "transaction"})
_OWNER_SCOPED_TRANSACTION_CALLS = frozenset({"begin", "commit", "rollback"})
_TRANSACTION_OWNERS = frozenset({"connection", "conn", "database", "db", "session", "transaction"})
_DATABASE_CALLS = frozenset(
    {
        "bulk_create",
        "bulk_update",
        "bulkcreate",
        "bulkupdate",
        "upsert",
    }
)
_OWNER_SCOPED_DATABASE_CALLS = frozenset(
    {"add", "create", "delete", "execute", "flush", "insert", "save", "update"}
)
_DATABASE_OWNERS = frozenset(
    {
        "connection",
        "conn",
        "cursor",
        "database",
        "db",
        "model",
        "objects",
        "query",
        "repo",
        "repository",
        "session",
    }
)
_NETWORK_ROOTS = frozenset(
    {
        "aiohttp",
        "axios",
        "http",
        "httpclient",
        "httpx",
        "processor",
        "requests",
        "stripe",
        "urllib",
        "webhook",
        "webhooks",
    }
)
_NETWORK_STANDALONE_CALLS = frozenset({"fetch", "request"})
_NETWORK_METHODS = frozenset(
    {"delete", "get", "head", "options", "patch", "post", "put", "request", "urlopen"}
)
_CONVENTIONAL_NETWORK_OWNERS = frozenset({"api", "client"})
_FILE_WRITE_CALLS = frozenset(
    {"appendfile", "createwritestream", "write_bytes", "write_text", "writefile"}
)
_FILE_WRITE_OWNERS = frozenset({"file", "fs", "handle", "output", "path", "stream", "writer"})
_MESSAGE_CALLS = frozenset(
    {"apply_async", "delay", "enqueue", "produce", "publish", "send_message", "sendmessage"}
)
_DESCRIPTIONS = {
    SideEffectKind.DATABASE_WRITE: "The changed code likely writes to a database.",
    SideEffectKind.TRANSACTION_BOUNDARY: (
        "The changed code appears to cross a local transaction boundary; external operations "
        "may not share that boundary."
    ),
    SideEffectKind.NETWORK_CALL: (
        "The changed code likely makes a network or external service call."
    ),
    SideEffectKind.FILE_WRITE: "The changed code likely writes to the local filesystem.",
    SideEffectKind.MESSAGE_PUBLISH: (
        "The changed code likely publishes or enqueues a message or job."
    ),
}
_MAX_PATCH_CALL_LINES = 20
_MAX_PATCH_CALL_CHARS = 4_096


class SideEffectDetector:
    """Attach cautious side-effect inferences to a change context."""

    def __init__(self, *, max_references: int = 5_000, max_effects: int = 100) -> None:
        if max_references < 1:
            raise ValueError("max_references must be positive")
        if max_effects < 1:
            raise ValueError("max_effects must be positive")
        self.max_references = max_references
        self.max_effects = max_effects

    def detect(self, context: ChangeContext) -> ChangeContext:
        """Return the context with bounded, evidence-backed inferences."""

        effects: list[LikelySideEffect] = []
        seen = self._existing_keys(context.likely_side_effects)
        omitted_effects = 0
        remaining_effect_capacity = max(
            0,
            self.max_effects - len(context.likely_side_effects),
        )

        for reference in context.call_sites[: self.max_references]:
            hunk = self._find_hunk(context.diff_hunks, reference)
            kind = self._classify(context, reference, hunk)
            if kind is None:
                continue
            key = (kind, reference.file_path, reference.name.lower(), hunk.header if hunk else None)
            if key in seen:
                continue
            seen.add(key)
            if len(effects) >= remaining_effect_capacity:
                omitted_effects += 1
                continue
            citation = EvidenceCitation(
                kind="call",
                file_path=reference.file_path,
                symbol=reference.name,
                hunk_header=hunk.header if hunk else None,
                line_start=reference.line_start,
                line_end=reference.line_start,
                note=self._evidence_note(hunk, reference.line_start),
            )
            effects.append(
                LikelySideEffect(
                    kind=kind,
                    description=_DESCRIPTIONS[kind],
                    evidence=(citation,),
                )
            )

        uncertainty_notes = list(context.uncertainty_notes)
        omitted_references = max(0, len(context.call_sites) - self.max_references)
        if omitted_references:
            noun = "reference" if omitted_references == 1 else "references"
            note = (
                f"Omitted {omitted_references:,} call {noun} from side-effect detection because "
                f"the scan limit is {self.max_references:,}."
            )
            if note not in uncertainty_notes:
                uncertainty_notes.append(note)
        if omitted_effects:
            noun = "signal" if omitted_effects == 1 else "signals"
            note = (
                f"Omitted {omitted_effects:,} likely side-effect {noun} because the output limit "
                f"is {self.max_effects:,}."
            )
            if note not in uncertainty_notes:
                uncertainty_notes.append(note)

        if not effects and tuple(uncertainty_notes) == context.uncertainty_notes:
            return context
        return replace(
            context,
            likely_side_effects=context.likely_side_effects + tuple(effects),
            uncertainty_notes=tuple(uncertainty_notes),
        )

    @staticmethod
    def _existing_keys(
        effects: tuple[LikelySideEffect, ...],
    ) -> set[tuple[SideEffectKind, Path, str, str | None]]:
        keys: set[tuple[SideEffectKind, Path, str, str | None]] = set()
        for effect in effects:
            for citation in effect.evidence:
                keys.add(
                    (
                        effect.kind,
                        citation.file_path,
                        (citation.symbol or "").lower(),
                        citation.hunk_header,
                    )
                )
        return keys

    @staticmethod
    def _classify(
        context: ChangeContext,
        reference: CodeReference,
        hunk: DiffHunk | None,
    ) -> SideEffectKind | None:
        if reference.kind != "call":
            return None
        parts = tuple(part for part in reference.name.lower().split(".") if part)
        if not parts:
            return None
        terminal = parts[-1]

        owners = parts[:-1]

        if terminal in _TRANSACTION_CALLS or (
            terminal in _OWNER_SCOPED_TRANSACTION_CALLS
            and any(owner in _TRANSACTION_OWNERS for owner in owners)
        ):
            return SideEffectKind.TRANSACTION_BOUNDARY
        if SideEffectDetector._is_network_call(context, reference, parts):
            return SideEffectKind.NETWORK_CALL
        if terminal in _MESSAGE_CALLS:
            return SideEffectKind.MESSAGE_PUBLISH
        if terminal in _FILE_WRITE_CALLS or (
            terminal == "write" and any(owner in _FILE_WRITE_OWNERS for owner in owners)
        ):
            return SideEffectKind.FILE_WRITE
        if terminal == "open" and SideEffectDetector._is_python_write_open(
            context,
            reference,
            hunk,
        ):
            return SideEffectKind.FILE_WRITE
        if terminal in _DATABASE_CALLS:
            return SideEffectKind.DATABASE_WRITE
        if terminal in _OWNER_SCOPED_DATABASE_CALLS and any(
            owner in _DATABASE_OWNERS for owner in owners
        ):
            return SideEffectKind.DATABASE_WRITE
        return None

    @staticmethod
    def _is_network_call(
        context: ChangeContext,
        reference: CodeReference,
        parts: tuple[str, ...],
    ) -> bool:
        terminal = parts[-1]
        if terminal in _NETWORK_STANDALONE_CALLS or any(part in _NETWORK_ROOTS for part in parts):
            return True
        if (
            len(parts) > 1
            and parts[0] in _CONVENTIONAL_NETWORK_OWNERS
            and terminal in _NETWORK_METHODS
        ):
            return True

        imports = {
            imported.local_name.lower(): imported.name.lower()
            for imported in context.nearby_imports
            if imported.file_path == reference.file_path
            and imported.local_name
            and SideEffectDetector._is_network_import(imported.name)
        }
        imported_name = imports.get(parts[0])
        if imported_name is None:
            return False
        if len(parts) > 1:
            return terminal in _NETWORK_METHODS
        imported_terminal = imported_name.rsplit(".", maxsplit=1)[-1]
        return imported_terminal in _NETWORK_METHODS

    @staticmethod
    def _is_network_import(name: str) -> bool:
        parts = (part for part in name.lower().lstrip(".").split(".") if part)
        return any(part in _NETWORK_ROOTS for part in parts)

    @staticmethod
    def _is_python_write_open(
        context: ChangeContext,
        reference: CodeReference,
        hunk: DiffHunk | None,
    ) -> bool:
        if hunk is None or reference.line_start is None:
            return False
        changed_file = next(
            (changed for changed in context.changed_files if changed.path == reference.file_path),
            None,
        )
        if changed_file is None:
            return False
        language = changed_file.language or (
            "python" if changed_file.path.suffix.lower() == ".py" else None
        )
        if language != "python":
            return False
        call_expression = SideEffectDetector._patch_open_call_at_new_number(
            hunk,
            reference.line_start,
        )
        if call_expression is None:
            return False
        mode = SideEffectDetector._python_open_mode(call_expression)
        return mode is not None and any(flag in mode for flag in "wax+")

    @staticmethod
    def _patch_open_call_at_new_number(hunk: DiffHunk, target_line: int) -> str | None:
        new_line = hunk.new_start or 0
        call_lines: list[str] = []
        call_chars = 0

        for patch_line in hunk.patch.split("\n")[1:]:
            if not patch_line or patch_line.startswith("\\"):
                continue
            if patch_line.startswith("-"):
                continue

            if new_line >= target_line:
                line = patch_line[1:]
                added_chars = len(line) + bool(call_lines)
                if (
                    len(call_lines) >= _MAX_PATCH_CALL_LINES
                    or call_chars + added_chars > _MAX_PATCH_CALL_CHARS
                ):
                    break
                call_lines.append(line)
                call_chars += added_chars
                call_text = "\n".join(call_lines)
                call_expression = SideEffectDetector._python_open_call_expression(call_text)
                if call_expression is not None:
                    return call_expression

            new_line += 1

        return None

    @staticmethod
    def _python_open_call_expression(source: str) -> str | None:
        open_start: tuple[int, int] | None = None
        depth = 0
        try:
            tokens = tokenize.generate_tokens(io.StringIO(source).readline)
            for token in tokens:
                if open_start is None:
                    if token.type == tokenize.NAME and token.string == "open":
                        open_start = token.start
                    continue
                if token.type != tokenize.OP:
                    continue
                if token.string == "(":
                    depth += 1
                elif token.string == ")" and depth:
                    depth -= 1
                    if depth == 0:
                        return SideEffectDetector._source_range(source, open_start, token.end)
        except (IndentationError, tokenize.TokenError):
            return None
        return None

    @staticmethod
    def _source_range(
        source: str,
        start: tuple[int, int],
        end: tuple[int, int],
    ) -> str:
        lines = source.split("\n")
        start_line, start_column = start
        end_line, end_column = end
        if start_line == end_line:
            return lines[start_line - 1][start_column:end_column]
        selected = [lines[start_line - 1][start_column:]]
        selected.extend(lines[start_line : end_line - 1])
        selected.append(lines[end_line - 1][:end_column])
        return "\n".join(selected)

    @staticmethod
    def _python_open_mode(call_expression: str) -> str | None:
        try:
            expression = ast.parse(call_expression, mode="eval").body
        except (SyntaxError, UnicodeError):
            return None
        if not isinstance(expression, ast.Call):
            return None

        mode_node: ast.expr | None = expression.args[1] if len(expression.args) > 1 else None
        for keyword in expression.keywords:
            if keyword.arg == "mode":
                mode_node = keyword.value
                break
        if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str):
            return mode_node.value
        return None

    @staticmethod
    def _find_hunk(
        hunks: tuple[DiffHunk, ...],
        reference: CodeReference,
    ) -> DiffHunk | None:
        if reference.line_start is None:
            return next((hunk for hunk in hunks if hunk.file_path == reference.file_path), None)
        for hunk in hunks:
            if hunk.file_path != reference.file_path or hunk.new_start is None:
                continue
            end_line = hunk.new_start + max(hunk.new_lines or 0, 1) - 1
            if hunk.new_start <= reference.line_start <= end_line:
                return hunk
        return None

    @staticmethod
    def _evidence_note(hunk: DiffHunk | None, line_start: int | None) -> str:
        if hunk is None or line_start is None:
            return "Call-shaped signal observed in bounded change evidence."
        patch_line = SideEffectDetector._patch_line_at_new_number(hunk, line_start)
        if patch_line is not None and patch_line[0] == "+":
            return "Call-shaped signal observed on an added line."
        return "Call-shaped signal observed in nearby context; inference is lower confidence."

    @staticmethod
    def _patch_line_at_new_number(
        hunk: DiffHunk,
        target_line: int,
    ) -> tuple[str, str] | None:
        new_line = hunk.new_start or 0
        for patch_line in hunk.patch.split("\n")[1:]:
            if not patch_line or patch_line.startswith("\\"):
                continue
            if patch_line.startswith("-"):
                continue
            if new_line == target_line:
                return patch_line[0], patch_line[1:]
            new_line += 1
        return None
