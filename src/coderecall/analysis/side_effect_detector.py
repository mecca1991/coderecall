"""Detect likely side effects from bounded change evidence."""

from __future__ import annotations

import re
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

_TRANSACTION_CALLS = frozenset({"atomic", "begin", "commit", "rollback", "transaction"})
_DATABASE_CALLS = frozenset(
    {
        "bulk_create",
        "bulk_update",
        "bulkcreate",
        "bulkupdate",
        "delete",
        "flush",
        "insert",
        "save",
        "update",
        "upsert",
    }
)
_DATABASE_CREATE_OWNERS = frozenset(
    {"database", "db", "model", "objects", "query", "repo", "repository", "session"}
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
        "webhook",
        "webhooks",
    }
)
_NETWORK_STANDALONE_CALLS = frozenset({"fetch", "request"})
_FILE_WRITE_CALLS = frozenset(
    {"appendfile", "createwritestream", "write", "write_bytes", "write_text", "writefile"}
)
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
_PYTHON_OPEN_MODE = re.compile(
    r"\bopen\s*\([^)]*(?:,\s*|mode\s*=\s*)(?P<quote>['\"])(?P<mode>[^'\"]+)(?P=quote)"
)


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

        for reference in context.call_sites[: self.max_references]:
            hunk = self._find_hunk(context.diff_hunks, reference)
            kind = self._classify(context, reference, hunk)
            if kind is None:
                continue
            key = (kind, reference.file_path, reference.name.lower(), hunk.header if hunk else None)
            if key in seen:
                continue
            seen.add(key)
            if len(effects) >= self.max_effects:
                omitted_effects += 1
                continue
            citation = EvidenceCitation(
                kind="call",
                file_path=reference.file_path,
                symbol=reference.name,
                hunk_header=hunk.header if hunk else None,
                line_start=reference.line_start,
                line_end=reference.line_start,
                note="Call-shaped signal observed in changed or nearby hunk evidence.",
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
            uncertainty_notes.append(
                f"Omitted {omitted_references:,} call {noun} from side-effect detection because "
                f"the scan limit is {self.max_references:,}."
            )
        if omitted_effects:
            noun = "signal" if omitted_effects == 1 else "signals"
            uncertainty_notes.append(
                f"Omitted {omitted_effects:,} likely side-effect {noun} because the output limit "
                f"is {self.max_effects:,}."
            )

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

        if terminal in _TRANSACTION_CALLS:
            return SideEffectKind.TRANSACTION_BOUNDARY
        if terminal in _NETWORK_STANDALONE_CALLS or any(part in _NETWORK_ROOTS for part in parts):
            return SideEffectKind.NETWORK_CALL
        if terminal in _MESSAGE_CALLS:
            return SideEffectKind.MESSAGE_PUBLISH
        if terminal in _FILE_WRITE_CALLS:
            return SideEffectKind.FILE_WRITE
        if terminal == "open" and SideEffectDetector._is_python_write_open(
            context,
            reference,
            hunk,
        ):
            return SideEffectKind.FILE_WRITE
        if terminal in _DATABASE_CALLS:
            return SideEffectKind.DATABASE_WRITE
        if terminal == "create" and any(part in _DATABASE_CREATE_OWNERS for part in parts[:-1]):
            return SideEffectKind.DATABASE_WRITE
        return None

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
        line = SideEffectDetector._line_at_new_number(hunk, reference.line_start)
        if line is None:
            return False
        match = _PYTHON_OPEN_MODE.search(line)
        return match is not None and any(flag in match.group("mode") for flag in "wax+")

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
    def _line_at_new_number(hunk: DiffHunk, target_line: int) -> str | None:
        new_line = hunk.new_start or 0
        for patch_line in hunk.patch.split("\n")[1:]:
            if not patch_line or patch_line.startswith("\\"):
                continue
            if patch_line.startswith("-"):
                continue
            if new_line == target_line:
                return patch_line[1:]
            new_line += 1
        return None
