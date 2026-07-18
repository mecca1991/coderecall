"""Tests for detecting likely side effects from change evidence."""

from __future__ import annotations

from pathlib import Path

from coderecall.analysis.side_effect_detector import SideEffectDetector
from coderecall.core.types import (
    ChangeContext,
    ChangedFile,
    CodeReference,
    DiffHunk,
    EvidenceCitation,
    FileStatus,
    LikelySideEffect,
    SideEffectKind,
)


def test_preserves_context_when_no_side_effect_signals_exist() -> None:
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/read-only-change",
        base_branch="main",
    )

    detected = SideEffectDetector().detect(context)

    assert detected is context
    assert detected.likely_side_effects == ()


def test_detects_each_supported_side_effect_from_call_evidence() -> None:
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/effects",
        base_branch="main",
        call_sites=(
            CodeReference(Path("service.py"), "call", "session.save", 10),
            CodeReference(Path("service.py"), "call", "transaction.atomic", 11),
            CodeReference(Path("client.py"), "call", "requests.post", 20),
            CodeReference(Path("export.py"), "call", "path.write_text", 30),
            CodeReference(Path("jobs.py"), "call", "queue.enqueue", 40),
        ),
    )

    detected = SideEffectDetector().detect(context)

    assert {effect.kind for effect in detected.likely_side_effects} == set(SideEffectKind)
    assert all(effect.evidence for effect in detected.likely_side_effects)
    assert all(
        "likely" in effect.description.lower() or "appears" in effect.description.lower()
        for effect in detected.likely_side_effects
    )


def test_detects_python_open_only_with_a_write_mode() -> None:
    write_hunk = DiffHunk(
        file_path=Path("writer.py"),
        header="@@ -1 +1 @@ def export():",
        new_start=1,
        new_lines=1,
        patch='@@ -1 +1 @@ def export():\n+    with open(path, "w") as output:\n',
    )
    read_hunk = DiffHunk(
        file_path=Path("reader.py"),
        header="@@ -1 +1 @@ def load():",
        new_start=1,
        new_lines=1,
        patch='@@ -1 +1 @@ def load():\n+    with open(path, "r") as source:\n',
    )
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/files",
        base_branch="main",
        changed_files=(
            ChangedFile(Path("writer.py"), FileStatus.MODIFIED, language="python"),
            ChangedFile(Path("reader.py"), FileStatus.MODIFIED, language="python"),
        ),
        diff_hunks=(write_hunk, read_hunk),
        call_sites=(
            CodeReference(Path("writer.py"), "call", "open", 1),
            CodeReference(Path("reader.py"), "call", "open", 1),
        ),
    )

    detected = SideEffectDetector().detect(context)

    assert len(detected.likely_side_effects) == 1
    effect = detected.likely_side_effects[0]
    assert effect.kind is SideEffectKind.FILE_WRITE
    assert effect.evidence[0].file_path == Path("writer.py")
    assert effect.evidence[0].hunk_header == write_hunk.header


def test_ignores_generic_identifier_substrings_and_non_call_references() -> None:
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/names",
        base_branch="main",
        call_sites=(
            CodeReference(Path("service.py"), "call", "database_update_message", 1),
            CodeReference(Path("service.py"), "call", "republishStatus", 2),
            CodeReference(Path("service.py"), "call", "fetching", 3),
            CodeReference(Path("service.py"), "import", "requests.post", 4),
        ),
    )

    detected = SideEffectDetector().detect(context)

    assert detected is context


def test_requires_recognized_receivers_for_ambiguous_method_names() -> None:
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/ordinary-methods",
        base_branch="main",
        call_sites=(
            CodeReference(Path("config.py"), "call", "config.update", 1),
            CodeReference(Path("response.py"), "call", "response.write", 2),
            CodeReference(Path("release.py"), "call", "git.commit", 3),
        ),
    )

    detected = SideEffectDetector().detect(context)

    assert detected is context


def test_detects_common_database_operations_with_recognized_receivers() -> None:
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/orm-writes",
        base_branch="main",
        call_sites=(
            CodeReference(Path("service.py"), "call", "session.add", 1),
            CodeReference(Path("service.py"), "call", "session.execute", 2),
            CodeReference(Path("service.py"), "call", "cursor.execute", 3),
        ),
    )

    detected = SideEffectDetector().detect(context)

    assert [effect.kind for effect in detected.likely_side_effects] == [
        SideEffectKind.DATABASE_WRITE,
        SideEffectKind.DATABASE_WRITE,
        SideEffectKind.DATABASE_WRITE,
    ]


def test_resolves_imported_http_clients_and_functions_per_file() -> None:
    client_path = Path("client.py")
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/http-clients",
        base_branch="main",
        nearby_imports=(
            CodeReference(client_path, "import", "requests", 1, local_name="r"),
            CodeReference(client_path, "import", "requests.post", 2, local_name="post"),
            CodeReference(client_path, "import", "httpx", 3, local_name="transport"),
            CodeReference(Path("other.py"), "import", "requests", 1, local_name="remote"),
        ),
        call_sites=(
            CodeReference(client_path, "call", "r.post", 4),
            CodeReference(client_path, "call", "post", 5),
            CodeReference(client_path, "call", "transport.get", 6),
            CodeReference(client_path, "call", "api.get", 7),
            CodeReference(client_path, "call", "client.post", 8),
            CodeReference(client_path, "call", "remote.post", 9),
        ),
    )

    detected = SideEffectDetector().detect(context)

    assert [effect.evidence[0].symbol for effect in detected.likely_side_effects] == [
        "r.post",
        "post",
        "transport.get",
        "api.get",
        "client.post",
    ]
    assert all(
        effect.kind is SideEffectKind.NETWORK_CALL
        for effect in detected.likely_side_effects
    )


def test_deduplicates_signals_and_preserves_existing_effects() -> None:
    hunk = DiffHunk(
        file_path=Path("client.py"),
        header="@@ -1,2 +1,2 @@ def send():",
        new_start=1,
        new_lines=2,
        patch="@@ -1,2 +1,2 @@ def send():\n+    requests.post(url)\n+    requests.post(url)\n",
    )
    existing = LikelySideEffect(
        kind=SideEffectKind.DATABASE_WRITE,
        description="The changed code likely writes to a database.",
        evidence=(EvidenceCitation("call", Path("db.py"), symbol="session.save"),),
    )
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/client",
        base_branch="main",
        diff_hunks=(hunk,),
        call_sites=(
            CodeReference(Path("client.py"), "call", "requests.post", 1),
            CodeReference(Path("client.py"), "call", "requests.post", 2),
        ),
        likely_side_effects=(existing,),
    )

    detected = SideEffectDetector().detect(context)

    assert detected.likely_side_effects[0] is existing
    assert [effect.kind for effect in detected.likely_side_effects].count(
        SideEffectKind.NETWORK_CALL
    ) == 1


def test_caps_scanned_references_and_reports_omissions() -> None:
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/many-calls",
        base_branch="main",
        call_sites=(
            CodeReference(Path("db.py"), "call", "session.save", 1),
            CodeReference(Path("client.py"), "call", "requests.post", 2),
        ),
    )

    detected = SideEffectDetector(max_references=1).detect(context)

    assert [effect.kind for effect in detected.likely_side_effects] == [
        SideEffectKind.DATABASE_WRITE
    ]
    assert any("Omitted 1 call reference" in note for note in detected.uncertainty_notes)


def test_effect_limit_is_idempotent_across_repeated_detection() -> None:
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/many-effects",
        base_branch="main",
        call_sites=(
            CodeReference(Path("db.py"), "call", "session.save", 1),
            CodeReference(Path("client.py"), "call", "requests.post", 2),
        ),
    )
    detector = SideEffectDetector(max_effects=1)

    first = detector.detect(context)
    second = detector.detect(first)

    assert second is first
    assert len(second.likely_side_effects) == 1
    assert (
        sum("Omitted 1 likely side-effect signal" in note for note in second.uncertainty_notes) == 1
    )


def test_marks_nearby_context_evidence_as_lower_confidence() -> None:
    hunk = DiffHunk(
        file_path=Path("client.py"),
        header="@@ -1,2 +1,3 @@ def send():",
        new_start=1,
        new_lines=3,
        patch=(
            "@@ -1,2 +1,3 @@ def send():\n"
            "+    enabled = True\n"
            "     requests.post(url)\n"
            "     return enabled\n"
        ),
    )
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/client",
        base_branch="main",
        diff_hunks=(hunk,),
        call_sites=(CodeReference(Path("client.py"), "call", "requests.post", 2),),
    )

    detected = SideEffectDetector().detect(context)

    assert "lower confidence" in (detected.likely_side_effects[0].evidence[0].note or "")
