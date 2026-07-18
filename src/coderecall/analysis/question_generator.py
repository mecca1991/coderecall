"""Generate deterministic learning questions from bounded change evidence."""

from __future__ import annotations

import json
from pathlib import Path

from coderecall.core.types import (
    ChangeContext,
    ChangedSymbol,
    EvidenceCitation,
    Question,
    QuestionCategory,
)


class QuestionGenerator:
    """Create one behavior, failure, and evidence question for a branch change."""

    def generate(self, context: ChangeContext) -> tuple[Question, ...]:
        if not context.changed_files:
            raise ValueError("Question generation requires at least one meaningful changed file.")

        changed_paths = {changed_file.path for changed_file in context.changed_files}
        primary_symbol = next(
            (symbol for symbol in context.changed_symbols if symbol.file_path in changed_paths),
            None,
        )
        primary_path = (
            primary_symbol.file_path
            if primary_symbol is not None
            else context.changed_files[0].path
        )
        primary_reference = self._primary_reference(primary_path, primary_symbol)
        area = self._format_area(primary_path, primary_symbol)

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
            Question(
                id="failure",
                category=QuestionCategory.FAILURE,
                prompt=(
                    f"What failure mode is most important for {area}, and how does the changed "
                    "code handle it?"
                ),
                rationale=(
                    f"Reasoning about failure behavior is necessary for the change in {area}."
                ),
                references=(primary_reference,),
            ),
            Question(
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
            ),
        )

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
