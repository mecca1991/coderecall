"""Tests for building a structured change context."""

from __future__ import annotations

from pathlib import Path

from coderecall.analysis.change_model_builder import ChangeModelBuilder
from coderecall.core.types import (
    ChangedFile,
    DiffCollection,
    DiffHunk,
    FileStatus,
    FilteredFile,
    FilterReason,
    RepositoryContext,
)


def test_builds_context_without_losing_diff_evidence(tmp_path: Path) -> None:
    source_path = tmp_path / "src" / "payments.py"
    source_path.parent.mkdir()
    source_path.write_text("ENABLED = True\n")
    hunk = DiffHunk(
        file_path=Path("src/payments.py"),
        header="@@ -1,2 +1,2 @@",
        old_start=1,
        old_lines=2,
        new_start=1,
        new_lines=2,
        patch="@@ -1,2 +1,2 @@\n-ENABLED = False\n+ENABLED = True\n",
    )
    changed_file = ChangedFile(
        path=Path("src/payments.py"),
        status=FileStatus.MODIFIED,
        language="python",
        hunks=(hunk,),
    )
    filtered_file = FilteredFile(
        path=Path("package-lock.json"),
        status=FileStatus.MODIFIED,
        reason=FilterReason.LOCKFILE,
    )
    diff = DiffCollection(
        merge_base="abc123",
        changed_files=(changed_file,),
        filtered_files=(filtered_file,),
        diff_hunks=(hunk,),
        uncertainty_notes=("An oversized file was skipped.",),
    )
    repository = RepositoryContext(
        root=tmp_path,
        current_branch="feature/payment-idempotency",
    )

    context = ChangeModelBuilder().build(repository, "main", diff)

    assert context.repo_root == tmp_path
    assert context.current_branch == "feature/payment-idempotency"
    assert context.base_branch == "main"
    assert context.merge_base == "abc123"
    assert context.changed_files == (changed_file,)
    assert context.filtered_files == (filtered_file,)
    assert context.diff_hunks == (hunk,)
    assert context.changed_symbols == ()
    assert context.nearby_imports == ()
    assert context.call_sites == ()
    assert context.related_tests == ()
    assert context.uncertainty_notes == ("An oversized file was skipped.",)


def test_classifies_languages_and_changed_tests_in_diff_order() -> None:
    changed_files = (
        ChangedFile(path=Path("src/payments.py"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("tests/unit/test_payments.py"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("web/checkout.tsx"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("web/__tests__/checkout.test.ts"), status=FileStatus.ADDED),
        ChangedFile(path=Path("src/contest.py"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("notes/change.txt"), status=FileStatus.ADDED),
    )
    diff = DiffCollection(merge_base="abc123", changed_files=changed_files)
    repository = RepositoryContext(root=Path("/repo"), current_branch="feature/checkout")

    context = ChangeModelBuilder().build(repository, "main", diff)

    by_path = {changed.path: changed for changed in context.changed_files}
    assert by_path[Path("src/payments.py")].language == "python"
    assert by_path[Path("tests/unit/test_payments.py")].language == "python"
    assert by_path[Path("web/checkout.tsx")].language == "typescript"
    assert by_path[Path("web/__tests__/checkout.test.ts")].language == "typescript"
    assert by_path[Path("notes/change.txt")].language is None
    assert by_path[Path("tests/unit/test_payments.py")].is_test is True
    assert by_path[Path("web/__tests__/checkout.test.ts")].is_test is True
    assert by_path[Path("src/contest.py")].is_test is False
    assert context.related_tests == (
        Path("tests/unit/test_payments.py"),
        Path("web/__tests__/checkout.test.ts"),
    )


def test_recognizes_test_filename_conventions_without_substring_matches() -> None:
    changed_files = (
        ChangedFile(path=Path("src/payment_test.py"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("web/checkout.spec.js"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("web/checkout.test.mjs"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("src/testimonial.py"), status=FileStatus.MODIFIED),
        ChangedFile(path=Path("web/specification.ts"), status=FileStatus.MODIFIED),
    )
    diff = DiffCollection(merge_base="abc123", changed_files=changed_files)
    repository = RepositoryContext(root=Path("/repo"), current_branch="feature/tests")

    context = ChangeModelBuilder().build(repository, "main", diff)

    assert context.related_tests == (
        Path("src/payment_test.py"),
        Path("web/checkout.spec.js"),
        Path("web/checkout.test.mjs"),
    )


def test_extracts_python_symbols_imports_and_calls_from_added_lines(tmp_path: Path) -> None:
    source_path = tmp_path / "src" / "payments.py"
    source_path.parent.mkdir()
    source_path.write_text(
        "import requests\n"
        "from payments.gateway import charge\n"
        "\n"
        "def unchanged():\n"
        "    return 1\n"
        "\n"
        "async def create_payment(amount):\n"
        "    response = charge(amount)\n"
        "    requests.post('/audit', json=response)\n"
        "    return response\n"
    )
    hunk = DiffHunk(
        file_path=Path("src/payments.py"),
        header="@@ -7,2 +7,4 @@ async def create_payment(amount):",
        old_start=7,
        old_lines=2,
        new_start=7,
        new_lines=4,
        patch=(
            "@@ -7,2 +7,4 @@ async def create_payment(amount):\n"
            " async def create_payment(amount):\n"
            "+    response = charge(amount)\n"
            "+    requests.post('/audit', json=response)\n"
            "     return response\n"
        ),
    )
    changed_file = ChangedFile(
        path=Path("src/payments.py"),
        status=FileStatus.MODIFIED,
        hunks=(hunk,),
    )
    diff = DiffCollection(
        merge_base="abc123",
        changed_files=(changed_file,),
        diff_hunks=(hunk,),
    )
    repository = RepositoryContext(root=tmp_path, current_branch="feature/payments")

    context = ChangeModelBuilder().build(repository, "main", diff)

    assert [
        (symbol.name, symbol.kind, symbol.line_start) for symbol in context.changed_symbols
    ] == [("create_payment", "async function", 7)]
    assert [(reference.name, reference.line_start) for reference in context.nearby_imports] == [
        ("requests", 1),
        ("payments.gateway.charge", 2),
    ]
    assert [(reference.name, reference.line_start) for reference in context.call_sites] == [
        ("charge", 8),
        ("requests.post", 9),
    ]
    assert context.uncertainty_notes == ()


def test_formats_relative_python_imports_without_extra_separator(tmp_path: Path) -> None:
    source_path = tmp_path / "src" / "module.py"
    source_path.parent.mkdir()
    source_path.write_text("from . import helper\n")
    hunk = DiffHunk(
        file_path=Path("src/module.py"),
        header="@@ -0,0 +1 @@",
        new_start=1,
        new_lines=1,
        patch="@@ -0,0 +1 @@\n+from . import helper\n",
    )
    diff = DiffCollection(
        merge_base="abc123",
        changed_files=(
            ChangedFile(path=Path("src/module.py"), status=FileStatus.ADDED, hunks=(hunk,)),
        ),
    )
    repository = RepositoryContext(root=tmp_path, current_branch="feature/import")

    context = ChangeModelBuilder().build(repository, "main", diff)

    assert [reference.name for reference in context.nearby_imports] == [".helper"]


def test_extracts_typescript_evidence_with_heuristic_uncertainty(tmp_path: Path) -> None:
    source_path = tmp_path / "web" / "payments.ts"
    source_path.parent.mkdir()
    source_path.write_text(
        "import { processor } from './processor';\n"
        "import { audit } from './audit';\n"
        "export async function createPayment(amount: number) {\n"
        "  const result = await processor.charge(amount);\n"
        "  audit.record(result);\n"
        "  if (result) {\n"
        "    return result;\n"
        "  }\n"
        "}\n"
    )
    patch_lines = (
        "+import { processor } from './processor';\n"
        "+import { audit } from './audit';\n"
        "+export async function createPayment(amount: number) {\n"
        "+  const result = await processor.charge(amount);\n"
        "+  audit.record(result);\n"
        "+  if (result) {\n"
        "+    return result;\n"
        "+  }\n"
        "+}\n"
    )
    hunk = DiffHunk(
        file_path=Path("web/payments.ts"),
        header="@@ -0,0 +1,9 @@",
        old_start=0,
        old_lines=0,
        new_start=1,
        new_lines=9,
        patch="@@ -0,0 +1,9 @@\n" + patch_lines,
    )
    diff = DiffCollection(
        merge_base="abc123",
        changed_files=(
            ChangedFile(
                path=Path("web/payments.ts"),
                status=FileStatus.ADDED,
                hunks=(hunk,),
            ),
        ),
        diff_hunks=(hunk,),
    )
    repository = RepositoryContext(root=tmp_path, current_branch="feature/payments")

    context = ChangeModelBuilder().build(repository, "main", diff)

    assert [(symbol.name, symbol.kind) for symbol in context.changed_symbols] == [
        ("createPayment", "async function")
    ]
    assert [reference.name for reference in context.nearby_imports] == [
        "./processor",
        "./audit",
    ]
    assert [reference.name for reference in context.call_sites] == [
        "processor.charge",
        "audit.record",
    ]
    assert any(
        "heuristic" in note and '"web/payments.ts"' in note for note in context.uncertainty_notes
    )


def test_distinguishes_typescript_methods_from_calls(tmp_path: Path) -> None:
    source_path = tmp_path / "web" / "payment-service.ts"
    source_path.parent.mkdir()
    source_path.write_text(
        "export class PaymentService {\n"
        "  async charge(amount: number) {\n"
        "    return processor.charge(amount);\n"
        "  }\n"
        "}\n"
    )
    hunk = DiffHunk(
        file_path=Path("web/payment-service.ts"),
        header="@@ -0,0 +1,5 @@",
        new_start=1,
        new_lines=5,
        patch=(
            "@@ -0,0 +1,5 @@\n"
            "+export class PaymentService {\n"
            "+  async charge(amount: number) {\n"
            "+    return processor.charge(amount);\n"
            "+  }\n"
            "+}\n"
        ),
    )
    diff = DiffCollection(
        merge_base="abc123",
        changed_files=(
            ChangedFile(
                path=Path("web/payment-service.ts"),
                status=FileStatus.ADDED,
                hunks=(hunk,),
            ),
        ),
    )
    repository = RepositoryContext(root=tmp_path, current_branch="feature/service")

    context = ChangeModelBuilder().build(repository, "main", diff)

    assert [(symbol.name, symbol.kind) for symbol in context.changed_symbols] == [
        ("PaymentService", "class"),
        ("charge", "async method"),
    ]
    assert [reference.name for reference in context.call_sites] == ["processor.charge"]


def test_identifies_python_symbol_for_deletion_only_hunk(tmp_path: Path) -> None:
    source_path = tmp_path / "src" / "service.py"
    source_path.parent.mkdir()
    source_path.write_text("def run():\n    return True\n")
    hunk = DiffHunk(
        file_path=Path("src/service.py"),
        header="@@ -1,3 +1,2 @@",
        old_start=1,
        old_lines=3,
        new_start=1,
        new_lines=2,
        patch="@@ -1,3 +1,2 @@\n def run():\n-    audit()\n     return True\n",
    )
    diff = DiffCollection(
        merge_base="abc123",
        changed_files=(
            ChangedFile(
                path=Path("src/service.py"),
                status=FileStatus.MODIFIED,
                hunks=(hunk,),
            ),
        ),
    )
    repository = RepositoryContext(root=tmp_path, current_branch="feature/remove-audit")

    context = ChangeModelBuilder().build(repository, "main", diff)

    assert [symbol.name for symbol in context.changed_symbols] == ["run"]


def test_identifies_typescript_symbol_for_body_only_edit(tmp_path: Path) -> None:
    source_path = tmp_path / "web" / "client.ts"
    source_path.parent.mkdir()
    source_path.write_text("export function run() {\n  return api.fetch();\n}\n")
    hunk = DiffHunk(
        file_path=Path("web/client.ts"),
        header="@@ -1,3 +1,3 @@",
        old_start=1,
        old_lines=3,
        new_start=1,
        new_lines=3,
        patch=(
            "@@ -1,3 +1,3 @@\n"
            " export function run() {\n"
            "-  return oldValue;\n"
            "+  return api.fetch();\n"
            " }\n"
        ),
    )
    diff = DiffCollection(
        merge_base="abc123",
        changed_files=(
            ChangedFile(
                path=Path("web/client.ts"),
                status=FileStatus.MODIFIED,
                hunks=(hunk,),
            ),
        ),
    )
    repository = RepositoryContext(root=tmp_path, current_branch="feature/client")

    context = ChangeModelBuilder().build(repository, "main", diff)

    assert [(symbol.name, symbol.kind) for symbol in context.changed_symbols] == [
        ("run", "function")
    ]
    assert [reference.name for reference in context.call_sites] == ["api.fetch"]


def test_uses_hunk_context_when_python_source_is_invalid(tmp_path: Path) -> None:
    source_path = tmp_path / "src" / "broken.py"
    source_path.parent.mkdir()
    source_path.write_text("def broken(:\n    pass\n")
    hunk = DiffHunk(
        file_path=Path("src/broken.py"),
        header="@@ -1,2 +1,2 @@ def broken(value):",
        old_start=1,
        old_lines=2,
        new_start=1,
        new_lines=2,
        patch="@@ -1,2 +1,2 @@ def broken(value):\n-def broken():\n+def broken(:\n     pass\n",
    )
    diff = DiffCollection(
        merge_base="abc123",
        changed_files=(
            ChangedFile(
                path=Path("src/broken.py"),
                status=FileStatus.MODIFIED,
                hunks=(hunk,),
            ),
        ),
    )
    repository = RepositoryContext(root=tmp_path, current_branch="feature/broken")

    context = ChangeModelBuilder().build(repository, "main", diff)

    assert [(symbol.name, symbol.kind) for symbol in context.changed_symbols] == [
        ("broken", "function")
    ]
    assert any("Could not parse" in note for note in context.uncertainty_notes)


def test_bounds_source_reads_and_reports_unsupported_languages(tmp_path: Path) -> None:
    large_path = tmp_path / "src" / "large.py"
    large_path.parent.mkdir()
    large_path.write_text("VALUE = '" + "x" * 100 + "'\n")
    go_path = tmp_path / "src" / "service.go"
    go_path.write_text("package service\n")
    hunk = DiffHunk(
        file_path=Path("src/service.go"),
        header="@@ -0,0 +1 @@",
        new_start=1,
        new_lines=1,
        patch="@@ -0,0 +1 @@\n+package service\n",
    )
    diff = DiffCollection(
        merge_base="abc123",
        changed_files=(
            ChangedFile(path=Path("src/large.py"), status=FileStatus.ADDED),
            ChangedFile(
                path=Path("src/service.go"),
                status=FileStatus.ADDED,
                hunks=(hunk,),
            ),
        ),
    )
    repository = RepositoryContext(root=tmp_path, current_branch="feature/bounds")

    context = ChangeModelBuilder(max_source_bytes=32).build(repository, "main", diff)

    assert context.changed_symbols == ()
    assert any(
        '"src/large.py"' in note and "exceeds 32 bytes" in note
        for note in context.uncertainty_notes
    )
    assert any(
        '"src/service.go"' in note and "not available" in note for note in context.uncertainty_notes
    )


def test_does_not_follow_source_paths_outside_repository(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    outside_source = tmp_path / "outside.py"
    outside_source.write_text("def private_symbol():\n    return 'private'\n")
    (repository_root / "linked.py").symlink_to(outside_source)
    hunk = DiffHunk(
        file_path=Path("linked.py"),
        header="@@ -0,0 +1,2 @@ def tracked_symbol():",
        new_start=1,
        new_lines=2,
        patch=(
            "@@ -0,0 +1,2 @@ def tracked_symbol():\n"
            "+def tracked_symbol():\n"
            "+    return 'diff evidence'\n"
        ),
    )
    diff = DiffCollection(
        merge_base="abc123",
        changed_files=(
            ChangedFile(path=Path("linked.py"), status=FileStatus.ADDED, hunks=(hunk,)),
        ),
    )
    repository = RepositoryContext(root=repository_root, current_branch="feature/symlink")

    context = ChangeModelBuilder().build(repository, "main", diff)

    assert [symbol.name for symbol in context.changed_symbols] == ["tracked_symbol"]
    assert any("outside the repository" in note for note in context.uncertainty_notes)


def test_does_not_parse_in_repository_symlink_targets(tmp_path: Path) -> None:
    target_path = tmp_path / "target.py"
    target_path.write_text("def unrelated():\n    return private_call()\n")
    (tmp_path / "linked.py").symlink_to(target_path.name)
    hunk = DiffHunk(
        file_path=Path("linked.py"),
        header="@@ -0,0 +1 @@ def tracked_symbol():",
        new_start=1,
        new_lines=1,
        patch="@@ -0,0 +1 @@ def tracked_symbol():\n+target.py\n",
    )
    diff = DiffCollection(
        merge_base="abc123",
        changed_files=(
            ChangedFile(path=Path("linked.py"), status=FileStatus.ADDED, hunks=(hunk,)),
        ),
    )
    repository = RepositoryContext(root=tmp_path, current_branch="feature/symlink")

    context = ChangeModelBuilder().build(repository, "main", diff)

    assert [symbol.name for symbol in context.changed_symbols] == ["tracked_symbol"]
    assert all(symbol.name != "unrelated" for symbol in context.changed_symbols)
    assert any("symlink" in note for note in context.uncertainty_notes)


def test_invalid_python_encoding_falls_back_to_hunk_context(tmp_path: Path) -> None:
    source_path = tmp_path / "broken.py"
    source_path.write_bytes(b"\xff\n")
    hunk = DiffHunk(
        file_path=Path("broken.py"),
        header="@@ -1,2 +1,2 @@ def broken():",
        old_start=1,
        old_lines=2,
        new_start=1,
        new_lines=2,
        patch=(
            '@@ -1,2 +1,2 @@ def broken():\n def broken():\n-    return "old"\n+    return "new"\n'
        ),
    )
    diff = DiffCollection(
        merge_base="abc123",
        changed_files=(
            ChangedFile(path=Path("broken.py"), status=FileStatus.MODIFIED, hunks=(hunk,)),
        ),
    )
    repository = RepositoryContext(root=tmp_path, current_branch="feature/encoding")

    context = ChangeModelBuilder().build(repository, "main", diff)

    assert [symbol.name for symbol in context.changed_symbols] == ["broken"]
    assert any("Could not parse" in note for note in context.uncertainty_notes)


def test_caps_retained_evidence_per_file(tmp_path: Path) -> None:
    source_path = tmp_path / "src" / "imports.py"
    source_path.parent.mkdir()
    source_path.write_text("import alpha\nimport beta\nimport gamma\n")
    hunk = DiffHunk(
        file_path=Path("src/imports.py"),
        header="@@ -0,0 +1,3 @@",
        new_start=1,
        new_lines=3,
        patch="@@ -0,0 +1,3 @@\n+import alpha\n+import beta\n+import gamma\n",
    )
    diff = DiffCollection(
        merge_base="abc123",
        changed_files=(
            ChangedFile(
                path=Path("src/imports.py"),
                status=FileStatus.ADDED,
                hunks=(hunk,),
            ),
        ),
    )
    repository = RepositoryContext(root=tmp_path, current_branch="feature/imports")

    context = ChangeModelBuilder(max_evidence_per_file=2).build(repository, "main", diff)

    assert [reference.name for reference in context.nearby_imports] == ["alpha", "beta"]
    assert any("Omitted 1 evidence item" in note for note in context.uncertainty_notes)
