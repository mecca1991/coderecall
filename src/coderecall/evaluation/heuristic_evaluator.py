"""Deterministic, offline evaluation of answers against changed-file evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from coderecall.core.types import (
    Answer,
    Assessment,
    AssessmentLabel,
    ChangeContext,
    EvidenceCitation,
    Question,
    QuestionCategory,
    SideEffectKind,
)

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD = re.compile(r"[^a-z0-9]+")
_CLAUSE_BOUNDARY = re.compile(r"[.!?;\n]+")
_NEGATIONS = frozenset({"cannot", "cant", "never", "no", "not", "without", "wont"})
_RECOVERY_TERMS = (
    "partial success",
    "retry",
    "retries",
    "recover",
    "reconcile",
    "compensat",
    "idempoten",
    "duplicate",
)
_SUPPORT_TERMS = (
    "assert",
    "check",
    "cover",
    "demonstrate",
    "evidence",
    "ensure",
    "prove",
    "return",
    "support",
    "verify",
)
_UNCOVERED = re.compile(
    r"\b(?:"
    r"(?:does|do|did|is|are|was|were) not (?:cover|test|verify|exercise)|"
    r"(?:doesnt|dont|didnt|isnt|arent|wasnt|werent) (?:cover|test|verify|exercise)|"
    r"not (?:covered|tested|verified)|un(?:covered|tested|verified)|"
    r"(?:missing|uncovered|unverified) (?:case|path|scenario|test|coverage)|"
    r"(?:no|lacks?) (?:test|coverage|evidence)|"
    r"(?:remains?|still) (?:uncovered|untested|unverified)"
    r")\b"
)
_ROLLBACK = re.compile(r"\b(?:rollback|roll back|rolls back|rolled back)\b")
_UNDO = re.compile(r"\b(?:undo(?:es|ne)?|revert(?:s|ed)?|reverse[ds]?|cancel(?:s|led)?)\b")
_PASSIVE_UNDO = r"(?:undone|reverted|reversed|cancell?ed)"
_OBJECT_PREFIX = r"(?:(?:a|an|cited|external|likely|the|this|that)\s+)*"
_EXTERNAL_EFFECTS = frozenset(
    {SideEffectKind.FILE_WRITE, SideEffectKind.MESSAGE_PUBLISH, SideEffectKind.NETWORK_CALL}
)
_GENERIC_WORDS = frozenset(
    {"app", "code", "file", "function", "index", "main", "source", "src", "test", "tests"}
)
_LEADING_WORDS = frozenset(
    {"add", "create", "fetch", "find", "get", "handle", "load", "process", "set", "update"}
)
_SUMMARIES = {
    AssessmentLabel.STRONG: (
        "This answer connects the relevant repository evidence and reasoning into useful review "
        "preparation."
    ),
    AssessmentLabel.PARTIAL: (
        "This answer identifies repository-backed details; review preparation would benefit "
        "from the remaining connections below."
    ),
    AssessmentLabel.GAP_FOUND: (
        "This answer includes a repository-grounded claim to revisit before review."
    ),
}


class Evaluator(Protocol):
    """Contract for evaluating one captured answer against repository evidence."""

    def evaluate(
        self,
        context: ChangeContext,
        question: Question,
        answer: Answer,
    ) -> Assessment:
        """Return evidence-grounded preparation notes for one answer."""


@dataclass(frozen=True)
class _Concept:
    citation: EvidenceCitation
    phrases: tuple[str, ...]
    effect: SideEffectKind | None = None

    @property
    def label(self) -> str:
        return self.citation.symbol or self.effect or self.citation.file_path.as_posix()


class HeuristicEvaluator:
    """Conservatively compare answer language with bounded repository signals."""

    def evaluate(
        self,
        context: ChangeContext,
        question: Question,
        answer: Answer,
    ) -> Assessment:
        if question.id != answer.question_id:
            raise ValueError("question and answer IDs must match")

        changed_paths = {changed_file.path for changed_file in context.changed_files}
        evidence = self._sanitize(question.references, changed_paths)
        if answer.skipped:
            return self._uncertain(
                question,
                evidence,
                "The answer was skipped, so no comparison with repository evidence was made.",
            )
        if not evidence:
            return self._uncertain(
                question,
                (),
                "The question has no citations to a changed file, so the answer cannot be "
                "grounded in this branch.",
            )

        text = self._normalize(answer.raw_text)
        cited = tuple(self._concept(context, citation) for citation in evidence)
        available = self._available_concepts(context, changed_paths)
        matched = self._deduplicate(
            tuple(concept for concept in cited + available if self._matches(text, concept))
        )
        if not matched:
            return self._uncertain(
                question,
                evidence,
                "The answer could not be matched responsibly to a repository concept in the "
                "available changed-file evidence.",
            )

        if question.category is QuestionCategory.BEHAVIOR:
            label, gaps, additions = self._behavior(text, cited, available)
            evidence = self._extend_evidence(evidence, additions)
        elif question.category is QuestionCategory.FAILURE:
            label, gaps = self._failure(text, cited)
        elif question.category is QuestionCategory.EVIDENCE:
            label, gaps = self._evidence(text, cited, context)
        else:
            label = AssessmentLabel.PARTIAL
            gaps = ("Connect the response explicitly to each cited changed-file detail.",)

        return Assessment(
            question_id=question.id,
            label=label,
            summary=_SUMMARIES[label],
            confidence="medium",
            strengths=tuple(
                f"Connects the answer to {self._format(concept)}." for concept in matched
            ),
            gaps=gaps,
            evidence=evidence,
        )

    def _behavior(
        self,
        text: str,
        cited: tuple[_Concept, ...],
        available: tuple[_Concept, ...],
    ) -> tuple[AssessmentLabel, tuple[str, ...], tuple[_Concept, ...]]:
        primary = cited[0]
        secondary = tuple(
            concept
            for concept in available
            if concept.citation.file_path == primary.citation.file_path
            and not self._same_source(concept.citation, primary.citation)
            and concept.citation.kind != "file"
        )
        matched_secondary = tuple(item for item in secondary if self._matches(text, item))
        missing_cited = tuple(item for item in cited if not self._matches(text, item))
        if not missing_cited and matched_secondary:
            return AssessmentLabel.STRONG, (), matched_secondary

        gaps = [
            f"Connect the behavior explanation directly to {self._format(concept)}."
            for concept in missing_cited
        ]
        if not matched_secondary:
            gaps.append(
                "Add another concrete repository detail from the same changed area to explain "
                "how the surrounding flow is affected."
            )
        return AssessmentLabel.PARTIAL, tuple(gaps), ()

    def _failure(
        self,
        text: str,
        cited: tuple[_Concept, ...],
    ) -> tuple[AssessmentLabel, tuple[str, ...]]:
        effects = tuple(concept for concept in cited if concept.effect is not None)
        if self._rollback_conflict(text, effects):
            return AssessmentLabel.GAP_FOUND, (
                "Revisit the rollback claim: the cited local transaction boundary does not "
                "itself reverse the likely external operation, so partial completion still "
                "needs review preparation.",
            )

        missing = tuple(concept for concept in cited if not self._matches(text, concept))
        recovery = any(term in text for term in _RECOVERY_TERMS)
        if effects and not missing and recovery:
            return AssessmentLabel.STRONG, ()

        gaps = [
            f"Explain how {self._format(concept)} participates in the risk boundary."
            for concept in missing
        ]
        if not effects:
            gaps.append(
                "The cited context has no structured side-effect boundary, so keep the risk "
                "claim bounded to the available evidence."
            )
        if not recovery:
            gaps.append(
                "Add the partial-completion, retry, recovery, or reconciliation implication for "
                "the cited boundaries."
            )
        return AssessmentLabel.PARTIAL, tuple(gaps)

    def _evidence(
        self,
        text: str,
        cited: tuple[_Concept, ...],
        context: ChangeContext,
    ) -> tuple[AssessmentLabel, tuple[str, ...]]:
        missing = tuple(concept for concept in cited if not self._matches(text, concept))
        support = any(term in text for term in _SUPPORT_TERMS)
        uncovered = _UNCOVERED.search(text) is not None
        structured = any(
            concept.citation.kind == "test"
            or concept.citation.symbol is not None
            or concept.citation.file_path in context.related_tests
            for concept in cited
        )
        if not missing and support and uncovered and structured:
            return AssessmentLabel.STRONG, ()

        gaps = [f"Tie the evidence explanation to {self._format(concept)}." for concept in missing]
        if not support:
            gaps.append("State which changed behavior the cited test or code path supports.")
        if not uncovered:
            gaps.append("Name a specific important path that remains uncovered or unverified.")
        if not structured:
            gaps.append(
                "The available citation is file-level only, so avoid claiming more coverage than "
                "the repository evidence shows."
            )
        return AssessmentLabel.PARTIAL, tuple(gaps)

    def _available_concepts(
        self,
        context: ChangeContext,
        changed_paths: set[Path],
    ) -> tuple[_Concept, ...]:
        concepts: list[_Concept] = []
        for effect in context.likely_side_effects:
            concepts.extend(
                self._concept(context, citation, effect.kind)
                for citation in self._sanitize(effect.evidence, changed_paths)
            )
        concepts.extend(
            self._concept(
                context,
                EvidenceCitation(
                    "symbol", symbol.file_path, symbol.name, line_start=symbol.line_start
                ),
            )
            for symbol in context.changed_symbols
            if symbol.file_path in changed_paths
        )
        concepts.extend(
            self._concept(
                context,
                EvidenceCitation(
                    reference.kind,
                    reference.file_path,
                    reference.name,
                    line_start=reference.line_start,
                ),
            )
            for reference in context.call_sites + context.nearby_imports
            if reference.file_path in changed_paths
        )
        concepts.extend(
            self._concept(context, EvidenceCitation("test", test_path))
            for test_path in context.related_tests
            if test_path in changed_paths
        )
        concepts.extend(
            self._concept(context, EvidenceCitation("file", changed_file.path))
            for changed_file in context.changed_files
        )
        return self._deduplicate(tuple(concepts))

    def _concept(
        self,
        context: ChangeContext,
        citation: EvidenceCitation,
        effect: SideEffectKind | None = None,
    ) -> _Concept:
        effect = effect or self._effect_for(context, citation)
        values = [
            citation.symbol or "",
            citation.file_path.as_posix(),
            effect.value if effect else "",
        ]
        if effect in _EXTERNAL_EFFECTS:
            values.extend(("external effect", "external operation"))
        phrases = tuple(
            dict.fromkeys(phrase for value in values for phrase in self._phrases(value))
        )
        return _Concept(citation, phrases, effect)

    @staticmethod
    def _effect_for(
        context: ChangeContext,
        citation: EvidenceCitation,
    ) -> SideEffectKind | None:
        for effect in context.likely_side_effects:
            if any(HeuristicEvaluator._same_source(citation, item) for item in effect.evidence):
                return effect.kind
        return None

    @staticmethod
    def _rollback_conflict(text: str, effects: tuple[_Concept, ...]) -> bool:
        external = tuple(concept for concept in effects if concept.effect in _EXTERNAL_EFFECTS)
        has_transaction = any(
            concept.effect is SideEffectKind.TRANSACTION_BOUNDARY for concept in effects
        )
        if not external or not has_transaction:
            return False

        for clause in _CLAUSE_BOUNDARY.split(text):
            if set(clause.split()) & _NEGATIONS:
                continue
            for concept in external:
                for phrase in concept.phrases:
                    escaped = re.escape(phrase)
                    direct_claim = re.compile(
                        rf"{_ROLLBACK.pattern}.{{0,60}}{_UNDO.pattern}\s+"
                        rf"{_OBJECT_PREFIX}\b{escaped}\b"
                    )
                    passive_claim = re.compile(
                        rf"\b{escaped}\b.{{0,30}}\b(?:is|gets|got|was|will be|would be)\s+"
                        rf"{_PASSIVE_UNDO}\b.{{0,30}}{_ROLLBACK.pattern}"
                    )
                    if direct_claim.search(clause) or passive_claim.search(clause):
                        return True
        return False

    @staticmethod
    def _sanitize(
        citations: tuple[EvidenceCitation, ...],
        changed_paths: set[Path],
    ) -> tuple[EvidenceCitation, ...]:
        return tuple(dict.fromkeys(item for item in citations if item.file_path in changed_paths))

    @staticmethod
    def _extend_evidence(
        evidence: tuple[EvidenceCitation, ...],
        additions: tuple[_Concept, ...],
    ) -> tuple[EvidenceCitation, ...]:
        extended = list(evidence)
        seen = {(item.file_path, item.symbol, item.kind) for item in evidence}
        for concept in additions:
            key = (concept.citation.file_path, concept.citation.symbol, concept.citation.kind)
            if key not in seen:
                extended.append(concept.citation)
                seen.add(key)
        return tuple(extended)

    @staticmethod
    def _deduplicate(concepts: tuple[_Concept, ...]) -> tuple[_Concept, ...]:
        unique: list[_Concept] = []
        seen: set[tuple[Path, str | None]] = set()
        for concept in concepts:
            key = (concept.citation.file_path, concept.citation.symbol)
            if key not in seen:
                unique.append(concept)
                seen.add(key)
        return tuple(unique)

    @staticmethod
    def _matches(text: str, concept: _Concept) -> bool:
        return any(re.search(rf"\b{re.escape(phrase)}\b", text) for phrase in concept.phrases)

    @staticmethod
    def _same_source(first: EvidenceCitation, second: EvidenceCitation) -> bool:
        return first.file_path == second.file_path and first.symbol == second.symbol

    @staticmethod
    def _phrases(value: str) -> tuple[str, ...]:
        if not value:
            return ()
        phrases = [HeuristicEvaluator._normalize(value)]
        path_name = Path(value).name
        if path_name != value:
            phrases.extend(
                (
                    HeuristicEvaluator._normalize(path_name),
                    HeuristicEvaluator._normalize(Path(path_name).stem),
                )
            )
        for segment in re.split(r"[./\\]", value):
            words = HeuristicEvaluator._normalize(segment).split()
            if not words:
                continue
            phrases.append(" ".join(words))
            while words and words[0] in _LEADING_WORDS:
                words.pop(0)
            if words[:1] == ["by"]:
                words.pop(0)
            if len(words) > 1 or (words and len(words[0]) >= 5 and words[0] not in _GENERIC_WORDS):
                phrases.append(" ".join(words))
        return tuple(dict.fromkeys(filter(None, phrases)))

    @staticmethod
    def _normalize(value: str) -> str:
        contractions = re.sub(
            r"\b(does|do|did|is|are|was|were|can|will)n['’]?t\b", r"\1 not", value
        )
        expanded = _CAMEL_BOUNDARY.sub(" ", contractions).lower()
        return _NON_WORD.sub(" ", expanded).strip()

    @staticmethod
    def _format(concept: _Concept) -> str:
        return f"`{concept.label}` in `{concept.citation.file_path.as_posix()}`"

    @staticmethod
    def _uncertain(
        question: Question,
        evidence: tuple[EvidenceCitation, ...],
        note: str,
    ) -> Assessment:
        return Assessment(
            question_id=question.id,
            label=AssessmentLabel.UNCERTAIN,
            summary="The available branch evidence does not support a responsible comparison.",
            confidence="low",
            evidence=evidence,
            uncertainty_notes=(note,),
        )
