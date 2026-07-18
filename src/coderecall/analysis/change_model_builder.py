"""Build a bounded model of the meaningful branch changes."""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path

from coderecall.core.types import (
    ChangeContext,
    ChangedFile,
    ChangedSymbol,
    CodeReference,
    DiffCollection,
    DiffHunk,
    RepositoryContext,
)

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
}
_TEST_DIRECTORIES = frozenset({"test", "tests", "__tests__"})


class ChangeModelBuilder:
    """Transform collected Git evidence into a change context."""

    def __init__(self, *, max_source_bytes: int = 256_000) -> None:
        if max_source_bytes < 1:
            raise ValueError("max_source_bytes must be positive")
        self.max_source_bytes = max_source_bytes

    def build(
        self,
        repository: RepositoryContext,
        base_branch: str,
        diff: DiffCollection,
    ) -> ChangeContext:
        """Preserve collected evidence in an immutable analysis context."""

        changed_files = tuple(
            self._classify_file(changed_file) for changed_file in diff.changed_files
        )
        related_tests = tuple(
            changed_file.path for changed_file in changed_files if changed_file.is_test
        )
        changed_symbols: list[ChangedSymbol] = []
        nearby_imports: list[CodeReference] = []
        call_sites: list[CodeReference] = []
        uncertainty_notes = list(diff.uncertainty_notes)

        for changed_file in changed_files:
            if changed_file.language != "python" or changed_file.is_binary:
                continue
            source, note = self._read_source(repository.root, changed_file.path)
            if note is not None:
                uncertainty_notes.append(note)
            if source is None:
                continue
            try:
                symbols, imports, calls = self._analyze_python(changed_file, source)
            except SyntaxError:
                uncertainty_notes.append(
                    "Could not parse "
                    f"{self._format_path(changed_file.path)} as Python; "
                    "symbol extraction may be incomplete."
                )
                continue
            changed_symbols.extend(symbols)
            nearby_imports.extend(imports)
            call_sites.extend(calls)

        return ChangeContext(
            repo_root=repository.root,
            current_branch=repository.current_branch,
            base_branch=base_branch,
            merge_base=diff.merge_base,
            changed_files=changed_files,
            filtered_files=diff.filtered_files,
            diff_hunks=diff.diff_hunks,
            changed_symbols=tuple(changed_symbols),
            nearby_imports=tuple(nearby_imports),
            call_sites=tuple(call_sites),
            related_tests=related_tests,
            uncertainty_notes=tuple(uncertainty_notes),
        )

    def _read_source(self, root: Path, relative_path: Path) -> tuple[str | None, str | None]:
        resolved_root = root.resolve()
        source_path = (resolved_root / relative_path).resolve()
        if not source_path.is_relative_to(resolved_root):
            return (
                None,
                f"Skipped source outside the repository: {self._format_path(relative_path)}.",
            )

        try:
            with source_path.open("rb") as source_file:
                content = source_file.read(self.max_source_bytes + 1)
        except OSError:
            return None, f"Could not read source file {self._format_path(relative_path)}."

        if len(content) > self.max_source_bytes:
            return (
                None,
                f"Skipped source analysis for {self._format_path(relative_path)} because the "
                f"file exceeds {self.max_source_bytes:,} bytes.",
            )
        return content.decode("utf-8", errors="surrogateescape"), None

    @staticmethod
    def _analyze_python(
        changed_file: ChangedFile,
        source: str,
    ) -> tuple[tuple[ChangedSymbol, ...], tuple[CodeReference, ...], tuple[CodeReference, ...]]:
        tree = ast.parse(source, filename=str(changed_file.path))
        added_lines = ChangeModelBuilder._added_line_numbers(changed_file.hunks)
        nodes = sorted(
            ast.walk(tree),
            key=lambda node: (getattr(node, "lineno", 0), getattr(node, "col_offset", 0)),
        )
        symbols: list[ChangedSymbol] = []
        imports: list[CodeReference] = []
        calls: list[CodeReference] = []

        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                end_line = node.end_lineno or node.lineno
                if any(node.lineno <= line <= end_line for line in added_lines):
                    kind = "class"
                    if isinstance(node, ast.AsyncFunctionDef):
                        kind = "async function"
                    elif isinstance(node, ast.FunctionDef):
                        kind = "function"
                    symbols.append(ChangedSymbol(changed_file.path, node.name, kind, node.lineno))
            elif isinstance(node, ast.Import):
                imports.extend(
                    CodeReference(changed_file.path, "import", alias.name, node.lineno)
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                module = "." * node.level + (node.module or "")
                imports.extend(
                    CodeReference(
                        changed_file.path,
                        "import",
                        f"{module}.{alias.name}" if module else alias.name,
                        node.lineno,
                    )
                    for alias in node.names
                )
            elif isinstance(node, ast.Call) and node.lineno in added_lines:
                name = ChangeModelBuilder._python_call_name(node.func)
                if name is not None:
                    calls.append(CodeReference(changed_file.path, "call", name, node.lineno))

        return tuple(symbols), tuple(imports), tuple(calls)

    @staticmethod
    def _added_line_numbers(hunks: tuple[DiffHunk, ...]) -> frozenset[int]:
        added_lines: set[int] = set()
        for hunk in hunks:
            new_line = hunk.new_start or 0
            for line in hunk.patch.split("\n")[1:]:
                if line.startswith("+"):
                    added_lines.add(new_line)
                    new_line += 1
                elif not line.startswith("-") and not line.startswith("\\"):
                    new_line += 1
        return frozenset(added_lines)

    @staticmethod
    def _python_call_name(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = ChangeModelBuilder._python_call_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return None

    @staticmethod
    def _format_path(path: Path) -> str:
        return json.dumps(str(path), ensure_ascii=True)

    @staticmethod
    def _classify_file(changed_file: ChangedFile) -> ChangedFile:
        language = changed_file.language or _LANGUAGES_BY_SUFFIX.get(
            changed_file.path.suffix.lower()
        )
        is_test = changed_file.is_test or ChangeModelBuilder._is_test_path(changed_file.path)
        return replace(changed_file, language=language, is_test=is_test)

    @staticmethod
    def _is_test_path(path: Path) -> bool:
        if any(part.lower() in _TEST_DIRECTORIES for part in path.parts[:-1]):
            return True

        name = path.name.lower()
        stem = path.stem.lower()
        if path.suffix.lower() == ".py":
            return stem.startswith("test_") or stem.endswith("_test")
        return ".test." in name or ".spec." in name
