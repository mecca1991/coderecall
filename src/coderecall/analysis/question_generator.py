"""Generate deterministic learning questions from bounded change evidence."""

from __future__ import annotations

import json
from pathlib import Path

from coderecall.core.types import (
    ChangeContext,
    ChangedSymbol,
    EvidenceCitation,
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
            raise ValueError("Question generation requires at least one meaningful changed file.")

        changed_paths = {changed_file.path for changed_file in context.changed_files}
        non_test_paths = {
            changed_file.path for changed_file in context.changed_files if not changed_file.is_test
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
                (
                    changed_file.path
                    for changed_file in context.changed_files
                    if not changed_file.is_test
                ),
                context.changed_files[0].path,
            )
        )
        primary_reference = self._primary_reference(primary_path, primary_symbol)
        area = self._format_area(primary_path, primary_symbol)
        valid_effects = self._valid_side_effects(context.likely_side_effects, changed_paths)
        changed_test_paths = {
            changed_file.path for changed_file in context.changed_files if changed_file.is_test
        }
        related_tests = tuple(
            dict.fromkeys(path for path in context.related_tests if path in changed_test_paths)
        )

        return (
            Question(
                id="behavior",
                category=QuestionCategory.BEHAVIOR,
                prompt=(
                    f"What behavior does {area} introduce or modify, and how does it affect "
                    "the surrounding flow?"
                ),
                rationale=f"The branch directly changes {area}.",
                references=(primary_reference,),
            ),
            self._failure_question(area, primary_reference, valid_effects),
            self._evidence_question(area, primary_reference, related_tests),
        )

    def _failure_question(
        self,
        area: str,
        primary_reference: EvidenceCitation,
        valid_effects: tuple[tuple[LikelySideEffect, tuple[EvidenceCitation, ...]], ...],
    ) -> Question:
        transaction = next(
            (item for item in valid_effects if item[0].kind is SideEffectKind.TRANSACTION_BOUNDARY),
            None,
        )
        external = next(
            (
                item
                for kind in _EXTERNAL_EFFECT_PRIORITY
                for item in valid_effects
                if item[0].kind is kind
            ),
            None,
        )
        if transaction is not None and external is not None:
            external_effect, external_evidence = external
            transaction_effect, transaction_evidence = transaction
            external_reference = external_evidence[0]
            transaction_reference = transaction_evidence[0]
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
                    f"most, and how does {area} handle it?"
                ),
                rationale=(
                    f"The changed code contains a repository-backed {effect.kind.value} signal."
                ),
                references=self._deduplicate_references((effect_reference, primary_reference)),
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
                    f"How does {formatted_test} provide evidence that {area} behaves as intended, "
                    "and which important path remains unverified?"
                ),
                rationale=(
                    f"The branch changes both {area} and the related test {formatted_test}."
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

    @staticmethod
    def _deduplicate_references(
        references: tuple[EvidenceCitation, ...],
    ) -> tuple[EvidenceCitation, ...]:
        return tuple(dict.fromkeys(references))
