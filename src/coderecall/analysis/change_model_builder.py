"""Build a bounded model of the meaningful branch changes."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import replace
from pathlib import Path

from coderecall.core.errors import CodeRecallError
from coderecall.core.types import (
    ChangeContext,
    ChangedFile,
    ChangedSymbol,
    CodeReference,
    DiffCollection,
    DiffHunk,
    RepositoryContext,
)
from coderecall.git.git_adapter import GitAdapter

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
_JS_FUNCTION = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:(async)\s+)?function\s+([A-Za-z_$][\w$]*)"
)
_JS_CLASS = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)")
_JS_ARROW = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
    r"(?:(async)\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=>"
)
_JS_EXPORT = re.compile(r"^\s*export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=")
_JS_METHOD = re.compile(
    r"^\s*(?P<modifiers>(?:(?:public|private|protected|static|readonly|abstract|override|async|get|set)\s+)*)"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?::[^{]+)?\{"
)
_JS_IMPORT_FROM = re.compile(r"^\s*import\s+.+?\s+from\s+['\"]([^'\"]+)['\"]")
_JS_IMPORT_SIDE_EFFECT = re.compile(r"^\s*import\s+['\"]([^'\"]+)['\"]")
_JS_REQUIRE = re.compile(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)")
_JS_CALL = re.compile(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(")
_NON_CALL_PREFIXES = frozenset({"catch", "class", "for", "function", "if", "switch", "while"})
_PYTHON_HUNK_SYMBOL = re.compile(r"^(?:(async)\s+)?(def|class)\s+([A-Za-z_]\w*)")


class ChangeModelBuilder:
    """Transform collected Git evidence into a change context."""

    def __init__(
        self,
        *,
        source_reader: GitAdapter | None = None,
        max_source_bytes: int = 256_000,
        max_evidence_per_file: int = 200,
    ) -> None:
        if max_source_bytes < 1:
            raise ValueError("max_source_bytes must be positive")
        if max_evidence_per_file < 1:
            raise ValueError("max_evidence_per_file must be positive")
        self.source_reader = source_reader
        self.max_source_bytes = max_source_bytes
        self.max_evidence_per_file = max_evidence_per_file

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
        unsupported_paths: list[Path] = []
        heuristic_paths: list[Path] = []

        for changed_file in changed_files:
            if changed_file.is_binary:
                continue
            if changed_file.language not in {"python", "javascript", "typescript"}:
                if changed_file.hunks:
                    unsupported_paths.append(changed_file.path)
                continue
            source, note = self._read_source(repository, diff, changed_file.path)
            if note is not None:
                uncertainty_notes.append(note)
            if source is None:
                symbols = self._symbols_from_hunk_context(changed_file)
                imports: tuple[CodeReference, ...] = ()
                calls: tuple[CodeReference, ...] = ()
            elif changed_file.language == "python":
                try:
                    symbols, imports, calls = self._analyze_python(changed_file, source)
                except (SyntaxError, ValueError):
                    symbols = self._symbols_from_hunk_context(changed_file)
                    imports = ()
                    calls = ()
                    uncertainty_notes.append(
                        "Could not parse "
                        f"{self._format_path(changed_file.path)} as Python; "
                        "symbol extraction may be incomplete."
                    )
            else:
                symbols, imports, calls = self._analyze_javascript(changed_file, source)
                heuristic_paths.append(changed_file.path)
            symbols, imports, calls, omitted = self._limit_evidence(symbols, imports, calls)
            if omitted:
                noun = "item" if omitted == 1 else "items"
                uncertainty_notes.append(
                    f"Omitted {omitted:,} evidence {noun} for "
                    f"{self._format_path(changed_file.path)} because the per-file limit is "
                    f"{self.max_evidence_per_file:,}."
                )
            changed_symbols.extend(symbols)
            nearby_imports.extend(imports)
            call_sites.extend(calls)

        if heuristic_paths:
            uncertainty_notes.append(
                "JavaScript/TypeScript symbol extraction is heuristic for: "
                f"{self._format_path_summary(heuristic_paths)}."
            )
        if unsupported_paths:
            uncertainty_notes.append(
                "Symbol extraction is not available for: "
                f"{self._format_path_summary(unsupported_paths)}."
            )

        return ChangeContext(
            repo_root=repository.root,
            current_branch=repository.current_branch,
            base_branch=base_branch,
            merge_base=diff.merge_base,
            changed_files=changed_files,
            filtered_files=diff.filtered_files,
            diff_hunks=diff.diff_hunks,
            changed_symbols=tuple(dict.fromkeys(changed_symbols)),
            nearby_imports=tuple(dict.fromkeys(nearby_imports)),
            call_sites=tuple(dict.fromkeys(call_sites)),
            related_tests=related_tests,
            uncertainty_notes=tuple(uncertainty_notes),
        )

    def _read_source(
        self,
        repository: RepositoryContext,
        diff: DiffCollection,
        relative_path: Path,
    ) -> tuple[str | None, str | None]:
        if not diff.includes_uncommitted and diff.source_revision is not None:
            return self._read_revision_source(repository, diff.source_revision, relative_path)
        return self._read_worktree_source(repository.root, relative_path)

    def _read_revision_source(
        self,
        repository: RepositoryContext,
        revision: str,
        relative_path: Path,
    ) -> tuple[str | None, str | None]:
        if self.source_reader is None:
            return (
                None,
                f"Could not read committed source file {self._format_path(relative_path)}.",
            )
        try:
            revision_file = self.source_reader.read_file_at_revision(
                repository,
                revision,
                relative_path,
                max_bytes=self.max_source_bytes,
            )
        except CodeRecallError:
            revision_file = None
        if revision_file is None:
            return None, f"Could not read source file {self._format_path(relative_path)}."
        if revision_file.content is None:
            return (
                None,
                f"Skipped source analysis for {self._format_path(relative_path)} because the "
                f"file exceeds {self.max_source_bytes:,} bytes.",
            )
        return revision_file.content.decode("utf-8", errors="surrogateescape"), None

    def _read_worktree_source(
        self,
        root: Path,
        relative_path: Path,
    ) -> tuple[str | None, str | None]:
        resolved_root = root.resolve()
        try:
            source_path = (resolved_root / relative_path).resolve()
        except (OSError, RuntimeError):
            return None, f"Could not resolve source file {self._format_path(relative_path)}."
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
        affected_lines = ChangeModelBuilder._affected_line_numbers(changed_file.hunks)
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
                if any(node.lineno <= line <= end_line for line in affected_lines):
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
                        ChangeModelBuilder._python_import_name(module, alias.name),
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
    def _analyze_javascript(
        changed_file: ChangedFile,
        source: str,
    ) -> tuple[tuple[ChangedSymbol, ...], tuple[CodeReference, ...], tuple[CodeReference, ...]]:
        added_lines = ChangeModelBuilder._added_line_numbers(changed_file.hunks)
        affected_lines = ChangeModelBuilder._affected_line_numbers(changed_file.hunks)
        symbols: list[ChangedSymbol] = []
        imports: list[CodeReference] = []
        calls: list[CodeReference] = []
        lines = source.split("\n")
        declarations: dict[int, ChangedSymbol] = {}

        for line_number, line in enumerate(lines, start=1):
            declaration = ChangeModelBuilder._javascript_declaration(
                changed_file,
                line,
                line_number,
            )
            if declaration is None:
                continue
            declarations[line_number] = declaration
            end_line = ChangeModelBuilder._javascript_block_end(lines, line_number)
            if any(line_number <= changed_line <= end_line for changed_line in affected_lines):
                symbols.append(declaration)

        for line_number, line in enumerate(lines, start=1):
            import_match = _JS_IMPORT_FROM.match(line) or _JS_IMPORT_SIDE_EFFECT.match(line)
            if import_match is not None:
                imports.append(
                    CodeReference(changed_file.path, "import", import_match.group(1), line_number)
                )
            for require_match in _JS_REQUIRE.finditer(line):
                imports.append(
                    CodeReference(
                        changed_file.path,
                        "import",
                        require_match.group(1),
                        line_number,
                    )
                )

            if line_number not in added_lines:
                continue
            declaration = declarations.get(line_number)
            declared_name = declaration.name if declaration is not None else None

            for call_match in _JS_CALL.finditer(line):
                call_name = call_match.group(1)
                prefix = line[: call_match.start()].rstrip().split()
                if (
                    call_name == declared_name
                    or call_name in _NON_CALL_PREFIXES
                    or (prefix and prefix[-1] in _NON_CALL_PREFIXES)
                ):
                    continue
                calls.append(
                    CodeReference(
                        changed_file.path,
                        "call",
                        call_name,
                        line_number,
                    )
                )

        if not symbols:
            symbols.extend(ChangeModelBuilder._symbols_from_hunk_context(changed_file))
        return tuple(symbols), tuple(imports), tuple(calls)

    @staticmethod
    def _javascript_declaration(
        changed_file: ChangedFile,
        line: str,
        line_number: int,
    ) -> ChangedSymbol | None:
        function_match = _JS_FUNCTION.match(line)
        if function_match is not None:
            kind = "async function" if function_match.group(1) else "function"
            return ChangedSymbol(changed_file.path, function_match.group(2), kind, line_number)

        class_match = _JS_CLASS.match(line)
        if class_match is not None:
            return ChangedSymbol(changed_file.path, class_match.group(1), "class", line_number)

        arrow_match = _JS_ARROW.match(line)
        if arrow_match is not None:
            kind = "async function" if arrow_match.group(2) else "function"
            return ChangedSymbol(changed_file.path, arrow_match.group(1), kind, line_number)

        export_match = _JS_EXPORT.match(line)
        if export_match is not None:
            return ChangedSymbol(changed_file.path, export_match.group(1), "export", line_number)

        method_match = _JS_METHOD.match(line)
        if method_match is None or method_match.group("name") in _NON_CALL_PREFIXES:
            return None
        modifiers = method_match.group("modifiers").split()
        kind = "async method" if "async" in modifiers else "method"
        return ChangedSymbol(changed_file.path, method_match.group("name"), kind, line_number)

    @staticmethod
    def _javascript_block_end(lines: list[str], declaration_line: int) -> int:
        depth = 0
        found_opening_brace = False
        for line_number in range(declaration_line, len(lines) + 1):
            line = lines[line_number - 1]
            opening_braces = line.count("{")
            closing_braces = line.count("}")
            if opening_braces:
                found_opening_brace = True
            if not found_opening_brace:
                continue
            depth += opening_braces - closing_braces
            if depth <= 0:
                return line_number
        return declaration_line

    @staticmethod
    def _symbols_from_hunk_context(changed_file: ChangedFile) -> tuple[ChangedSymbol, ...]:
        symbols: list[ChangedSymbol] = []
        for hunk in changed_file.hunks:
            header_parts = hunk.header.split("@@", 2)
            if len(header_parts) < 3:
                continue
            context = header_parts[2].strip()
            python_match = _PYTHON_HUNK_SYMBOL.match(context)
            if python_match is not None:
                kind = "class" if python_match.group(2) == "class" else "function"
                if python_match.group(1):
                    kind = "async function"
                symbols.append(
                    ChangedSymbol(
                        changed_file.path,
                        python_match.group(3),
                        kind,
                        hunk.new_start,
                    )
                )
                continue
            function_match = _JS_FUNCTION.match(context)
            if function_match is not None:
                kind = "async function" if function_match.group(1) else "function"
                symbols.append(
                    ChangedSymbol(
                        changed_file.path,
                        function_match.group(2),
                        kind,
                        hunk.new_start,
                    )
                )
        return tuple(symbols)

    @staticmethod
    def _added_line_numbers(hunks: tuple[DiffHunk, ...]) -> frozenset[int]:
        added_lines: set[int] = set()
        for hunk in hunks:
            new_line = hunk.new_start or 0
            for line in hunk.patch.split("\n")[1:]:
                if not line:
                    continue
                if line.startswith("+"):
                    added_lines.add(new_line)
                    new_line += 1
                elif not line.startswith("-") and not line.startswith("\\"):
                    new_line += 1
        return frozenset(added_lines)

    @staticmethod
    def _affected_line_numbers(hunks: tuple[DiffHunk, ...]) -> frozenset[int]:
        affected_lines: set[int] = set()
        for hunk in hunks:
            new_line = hunk.new_start or 0
            for line in hunk.patch.split("\n")[1:]:
                if not line:
                    continue
                if line.startswith("+"):
                    affected_lines.add(new_line)
                    new_line += 1
                elif line.startswith("-"):
                    if new_line > 0:
                        affected_lines.add(new_line)
                    if new_line > 1:
                        affected_lines.add(new_line - 1)
                elif not line.startswith("\\"):
                    new_line += 1
        return frozenset(affected_lines)

    @staticmethod
    def _python_call_name(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = ChangeModelBuilder._python_call_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return None

    @staticmethod
    def _python_import_name(module: str, imported_name: str) -> str:
        if not module:
            return imported_name
        separator = "" if module.endswith(".") else "."
        return f"{module}{separator}{imported_name}"

    @staticmethod
    def _format_path(path: Path) -> str:
        return json.dumps(str(path), ensure_ascii=True)

    @staticmethod
    def _format_path_summary(paths: list[Path]) -> str:
        shown_paths = paths[:5]
        summary = ", ".join(ChangeModelBuilder._format_path(path) for path in shown_paths)
        remaining = len(paths) - len(shown_paths)
        if remaining:
            summary += f", and {remaining:,} more"
        return summary

    def _limit_evidence(
        self,
        symbols: tuple[ChangedSymbol, ...],
        imports: tuple[CodeReference, ...],
        calls: tuple[CodeReference, ...],
    ) -> tuple[
        tuple[ChangedSymbol, ...],
        tuple[CodeReference, ...],
        tuple[CodeReference, ...],
        int,
    ]:
        remaining = self.max_evidence_per_file
        kept_symbols = symbols[:remaining]
        remaining -= len(kept_symbols)
        kept_calls = calls[:remaining]
        remaining -= len(kept_calls)
        kept_imports = imports[:remaining]
        omitted = (
            len(symbols)
            + len(imports)
            + len(calls)
            - len(kept_symbols)
            - len(kept_imports)
            - len(kept_calls)
        )
        return kept_symbols, kept_imports, kept_calls, omitted

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
