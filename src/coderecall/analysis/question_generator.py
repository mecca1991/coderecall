"""Generate deterministic learning questions from bounded change evidence."""

from __future__ import annotations

import json
from pathlib import Path

from coderecall.core.errors import QuestionGenerationUnavailable
from coderecall.core.types import (
    ChangeContext,
    ChangedFile,
    ChangedSymbol,
    EvidenceCitation,
    FileStatus,
    LikelySideEffect,
    Question,
    QuestionCategory,
    SideEffectKind,
)

_EXTERNAL_EFFECT_PRIORITY = (
    SideEffectKind.NETWORK_CALL,
    SideEffectKind.MESSAGE_PUBLISH,
    SideEffectKind.FILE_WRITE,
)


class QuestionGenerator:
    """Create one behavior, failure, and evidence question for a branch change."""

    def generate(self, context: ChangeContext) -> tuple[Question, ...]:
        if not context.changed_files:
            raise QuestionGenerationUnavailable(
                "Question generation requires at least one meaningful changed file."
            )

        analysis_files = self._analyzable_files(context)
        if not analysis_files:
            raise QuestionGenerationUnavailable(
                "Question generation requires analyzable change evidence."
            )

        changed_paths = {changed_file.path for changed_file in analysis_files}
        non_test_paths = {
            changed_file.path for changed_file in analysis_files if not changed_file.is_test
        }
        valid_symbols = tuple(
            symbol for symbol in context.changed_symbols if symbol.file_path in changed_paths
        )
        primary_symbol = next(
            (symbol for symbol in valid_symbols if symbol.file_path in non_test_paths),
            valid_symbols[0] if valid_symbols else None,
        )
        primary_path = (
            primary_symbol.file_path
            if primary_symbol is not None
            else next(
                (changed_file.path for changed_file in analysis_files if not changed_file.is_test),
                analysis_files[0].path,
            )
        )
        primary_reference = self._primary_reference(primary_path, primary_symbol)
        primary_file = next(
            changed_file for changed_file in analysis_files if changed_file.path == primary_path
        )
        area = self._format_area(primary_path, primary_symbol)
        valid_effects = self._valid_side_effects(context.likely_side_effects, changed_paths)
        changed_test_paths = {
            changed_file.path for changed_file in analysis_files if changed_file.is_test
        }
        related_tests = tuple(
            dict.fromkeys(path for path in context.related_tests if path in changed_test_paths)
        )

        return (
            self._behavior_question(area, primary_reference, primary_file),
            self._failure_question(area, primary_reference, valid_effects),
            self._evidence_question(area, primary_reference, related_tests),
        )

    @staticmethod
    def _analyzable_files(context: ChangeContext) -> tuple[ChangedFile, ...]:
        structured_paths = {symbol.file_path for symbol in context.changed_symbols}
        structured_paths.update(reference.file_path for reference in context.nearby_imports)
        structured_paths.update(reference.file_path for reference in context.call_sites)
        structured_paths.update(
            citation.file_path
            for side_effect in context.likely_side_effects
            for citation in side_effect.evidence
        )
        structured_paths.update(context.related_tests)
        hunk_paths = {hunk.file_path for hunk in context.diff_hunks}
        return tuple(
            changed_file
            for changed_file in context.changed_files
            if not changed_file.is_binary
            and (
                bool(changed_file.hunks)
                or changed_file.path in hunk_paths
                or changed_file.path in structured_paths
            )
        )

    @staticmethod
    def _behavior_question(
        area: str,
        primary_reference: EvidenceCitation,
        primary_file: ChangedFile,
    ) -> Question:
        if primary_file.status is FileStatus.DELETED:
            prompt = (
                f"What behavior does removing {area} eliminate, and how does that affect the "
                "surrounding flow?"
            )
            rationale = f"The branch removes {area}."
        elif primary_file.status is FileStatus.ADDED:
            prompt = (
                f"What behavior does {area} introduce, and how does it affect the surrounding flow?"
            )
            rationale = f"The branch adds {area}."
        elif primary_file.status is FileStatus.RENAMED:
            prompt = (
                f"What behavior, if any, changes when {area} is renamed, and how does the move "
                "affect the surrounding flow?"
            )
            rationale = f"The branch renames {area}."
        else:
            prompt = (
                f"What behavior does {area} modify, and how does it affect the surrounding flow?"
            )
            rationale = f"The branch modifies {area}."
        return Question(
            id="behavior",
            category=QuestionCategory.BEHAVIOR,
            prompt=prompt,
            rationale=rationale,
            references=(primary_reference,),
        )

    def _failure_question(
        self,
        area: str,
        primary_reference: EvidenceCitation,
        valid_effects: tuple[tuple[LikelySideEffect, tuple[EvidenceCitation, ...]], ...],
    ) -> Question:
        partial_success = self._partial_success_pair(valid_effects)
        if partial_success is not None:
            (
                external_effect,
                external_reference,
                transaction_effect,
                transaction_reference,
            ) = partial_success
            return Question(
                id="failure",
                category=QuestionCategory.FAILURE,
                prompt=(
                    f"If the likely {external_effect.kind.value} at "
                    f"{self._format_citation_area(external_reference)} succeeds but the "
                    f"{transaction_effect.kind.value} at "
                    f"{self._format_citation_area(transaction_reference)} does not complete, "
                    f"what state can remain, and how does {area} handle retry or recovery?"
                ),
                rationale=(
                    f"The branch has both {external_effect.kind.value} and "
                    f"{transaction_effect.kind.value} signals, so partial success may cross "
                    "a rollback boundary."
                ),
                references=(external_reference, transaction_reference),
            )

        if valid_effects:
            effect, evidence = valid_effects[0]
            effect_reference = evidence[0]
            return Question(
                id="failure",
                category=QuestionCategory.FAILURE,
                prompt=(
                    f"The branch contains a likely {effect.kind.value} at "
                    f"{self._format_citation_area(effect_reference)}. What failure mode matters "
                    "most, and how should the changed flow account for it?"
                ),
                rationale=(
                    f"The changed code contains a repository-backed {effect.kind.value} signal."
                ),
                references=(effect_reference,),
            )

        return Question(
            id="failure",
            category=QuestionCategory.FAILURE,
            prompt=(
                f"What failure mode is most important for {area}, and how does the changed "
                "code handle it?"
            ),
            rationale=f"Reasoning about failure behavior is necessary for the change in {area}.",
            references=(primary_reference,),
        )

    @staticmethod
    def _partial_success_pair(
        valid_effects: tuple[tuple[LikelySideEffect, tuple[EvidenceCitation, ...]], ...],
    ) -> (
        tuple[
            LikelySideEffect,
            EvidenceCitation,
            LikelySideEffect,
            EvidenceCitation,
        ]
        | None
    ):
        transactions = tuple(
            item for item in valid_effects if item[0].kind is SideEffectKind.TRANSACTION_BOUNDARY
        )
        for external_kind in _EXTERNAL_EFFECT_PRIORITY:
            for external_effect, external_evidence in valid_effects:
                if external_effect.kind is not external_kind:
                    continue
                for transaction_effect, transaction_evidence in transactions:
                    for external_reference in external_evidence:
                        for transaction_reference in transaction_evidence:
                            if QuestionGenerator._same_change_area(
                                external_reference,
                                transaction_reference,
                            ):
                                return (
                                    external_effect,
                                    external_reference,
                                    transaction_effect,
                                    transaction_reference,
                                )
        return None

    @staticmethod
    def _same_change_area(
        first: EvidenceCitation,
        second: EvidenceCitation,
    ) -> bool:
        return (
            first.file_path == second.file_path
            and first.hunk_header is not None
            and first.hunk_header == second.hunk_header
        )

    @staticmethod
    def _evidence_question(
        area: str,
        primary_reference: EvidenceCitation,
        related_tests: tuple[Path, ...],
    ) -> Question:
        if related_tests:
            test_path = related_tests[0]
            test_reference = EvidenceCitation(kind="test", file_path=test_path)
            formatted_test = json.dumps(str(test_path), ensure_ascii=True)
            return Question(
                id="evidence",
                category=QuestionCategory.EVIDENCE,
                prompt=(
                    f"What evidence, if any, does {formatted_test} provide for the behavior of "
                    f"{area}, and which important path remains unverified?"
                ),
                rationale=(
                    f"The branch changes both {area} and {formatted_test}; their relationship "
                    "should be established from repository evidence."
                ),
                references=(primary_reference, test_reference),
            )

        return Question(
            id="evidence",
            category=QuestionCategory.EVIDENCE,
            prompt=(
                "Which test, invariant, or code path provides the strongest evidence that "
                f"{area} behaves as intended?"
            ),
            rationale=(
                f"The intended behavior of {area} should be supported by repository evidence."
            ),
            references=(primary_reference,),
        )

    @staticmethod
    def _valid_side_effects(
        side_effects: tuple[LikelySideEffect, ...],
        changed_paths: set[Path],
    ) -> tuple[tuple[LikelySideEffect, tuple[EvidenceCitation, ...]], ...]:
        valid_effects = []
        for side_effect in side_effects:
            evidence = tuple(
                dict.fromkeys(
                    citation
                    for citation in side_effect.evidence
                    if citation.file_path in changed_paths
                )
            )
            if evidence:
                valid_effects.append((side_effect, evidence))
        return tuple(valid_effects)

    @staticmethod
    def _primary_reference(
        path: Path,
        symbol: ChangedSymbol | None,
    ) -> EvidenceCitation:
        if symbol is None:
            return EvidenceCitation(kind="file", file_path=path)
        return EvidenceCitation(
            kind="symbol",
            file_path=path,
            symbol=symbol.name,
            line_start=symbol.line_start,
        )

    @staticmethod
    def _format_area(path: Path, symbol: ChangedSymbol | None) -> str:
        formatted_path = json.dumps(str(path), ensure_ascii=True)
        if symbol is None:
            return formatted_path
        return f"`{symbol.name}` in {formatted_path}"

    @staticmethod
    def _format_citation_area(citation: EvidenceCitation) -> str:
        formatted_path = json.dumps(str(citation.file_path), ensure_ascii=True)
        if citation.symbol is None:
            return formatted_path
        return f"`{citation.symbol}` in {formatted_path}"
