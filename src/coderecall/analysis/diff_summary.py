"""Build concise, deterministic summaries from repository change evidence."""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from pathlib import Path
from typing import TypeVar

from coderecall.core.types import (
    ChangeContext,
    ChangedFile,
    ChangedSymbol,
    DiffSummary,
    FileStatus,
    LikelySideEffect,
    SideEffectKind,
)

_MAX_RELEVANT_FILES = 5
T = TypeVar("T", bound=Hashable)


class DiffSummaryService:
    """Summarize bounded change evidence without claiming runtime certainty."""

    def summarize(self, context: ChangeContext) -> DiffSummary:
        changed_paths = {changed_file.path for changed_file in context.changed_files}
        symbols = tuple(
            symbol for symbol in context.changed_symbols if symbol.file_path in changed_paths
        )
        side_effects = self._sanitize_side_effects(context.likely_side_effects, changed_paths)

        return DiffSummary(
            purpose=self._purpose(context.changed_files, symbols, side_effects),
            relevant_files=self._relevant_files(context.changed_files, symbols, side_effects),
            tests=self._deduplicate(
                path for path in context.related_tests if path in changed_paths
            ),
            side_effects=side_effects,
            uncertainty_notes=self._deduplicate(context.uncertainty_notes),
        )

    @staticmethod
    def _purpose(
        changed_files: tuple[ChangedFile, ...],
        symbols: tuple[ChangedSymbol, ...],
        side_effects: tuple[LikelySideEffect, ...],
    ) -> str:
        if not changed_files:
            return "No meaningful code changes were available to summarize."

        file_count = len(changed_files)
        file_label = "meaningful file" if file_count == 1 else "meaningful files"
        symbol_names = DiffSummaryService._deduplicate(symbol.name for symbol in symbols)
        if symbol_names:
            shown_symbols = " and ".join(f"`{name}`" for name in symbol_names[:2])
            purpose = f"Likely updates {shown_symbols} across {file_count} {file_label}"
        else:
            action = DiffSummaryService._status_action(changed_files)
            purpose = f"Likely {action} code in {file_count} {file_label}"

        effect_kinds = tuple(effect.kind for effect in side_effects)
        if effect_kinds:
            purpose += f", with {DiffSummaryService._effect_phrase(effect_kinds)}"
        return purpose + "."

    @staticmethod
    def _status_action(changed_files: tuple[ChangedFile, ...]) -> str:
        statuses = {changed_file.status for changed_file in changed_files}
        if statuses == {FileStatus.ADDED}:
            return "adds"
        if statuses == {FileStatus.DELETED}:
            return "removes"
        if statuses == {FileStatus.RENAMED}:
            return "reorganizes"
        return "updates"

    @staticmethod
    def _effect_phrase(kinds: tuple[SideEffectKind, ...]) -> str:
        labels = [kind.value for kind in kinds[:2]]
        if len(kinds) == 1:
            return f"a {labels[0]} signal"
        joined = " and ".join(labels)
        if len(kinds) > 2:
            joined += " and other side-effect"
        return f"{joined} signals"

    @staticmethod
    def _relevant_files(
        changed_files: tuple[ChangedFile, ...],
        symbols: tuple[ChangedSymbol, ...],
        side_effects: tuple[LikelySideEffect, ...],
    ) -> tuple[Path, ...]:
        symbol_paths = {symbol.file_path for symbol in symbols}
        effect_paths = {
            citation.file_path for effect in side_effects for citation in effect.evidence
        }

        def rank(item: tuple[int, ChangedFile]) -> tuple[int, int]:
            index, changed_file = item
            if changed_file.path in effect_paths:
                category = 0
            elif changed_file.path in symbol_paths:
                category = 1
            elif not changed_file.is_test:
                category = 2
            else:
                category = 3
            return category, index

        ranked = sorted(enumerate(changed_files), key=rank)
        return tuple(changed_file.path for _, changed_file in ranked[:_MAX_RELEVANT_FILES])

    @staticmethod
    def _sanitize_side_effects(
        side_effects: tuple[LikelySideEffect, ...],
        changed_paths: set[Path],
    ) -> tuple[LikelySideEffect, ...]:
        by_kind: dict[SideEffectKind, LikelySideEffect] = {}
        for side_effect in side_effects:
            valid_evidence = DiffSummaryService._deduplicate(
                citation for citation in side_effect.evidence if citation.file_path in changed_paths
            )
            if not valid_evidence:
                continue
            previous = by_kind.get(side_effect.kind)
            if previous is None:
                by_kind[side_effect.kind] = LikelySideEffect(
                    kind=side_effect.kind,
                    description=side_effect.description,
                    evidence=valid_evidence,
                )
                continue
            by_kind[side_effect.kind] = LikelySideEffect(
                kind=previous.kind,
                description=previous.description,
                evidence=DiffSummaryService._deduplicate((*previous.evidence, *valid_evidence)),
            )
        return tuple(by_kind.values())

    @staticmethod
    def _deduplicate(items: Iterable[T]) -> tuple[T, ...]:
        return tuple(dict.fromkeys(items))
