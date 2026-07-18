"""Integration tests for building context from a real Git diff."""

from __future__ import annotations

import subprocess
from pathlib import Path

from coderecall.analysis import ChangeModelBuilder, FileFilter
from coderecall.git import DiffCollector, GitAdapter


def run_git(directory: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=directory,
        capture_output=True,
        check=True,
        text=True,
    )


def commit_all(directory: Path, message: str) -> None:
    run_git(directory, "add", "--all")
    run_git(directory, "commit", "--quiet", "-m", message)


def test_builds_python_and_typescript_context_from_real_diff(tmp_path: Path) -> None:
    run_git(tmp_path, "init", "--quiet")
    run_git(tmp_path, "checkout", "--quiet", "-b", "main")
    run_git(tmp_path, "config", "user.name", "CodeRecall Tests")
    run_git(tmp_path, "config", "user.email", "tests@coderecall.local")
    (tmp_path / "src").mkdir()
    (tmp_path / "web").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "service.py").write_text("def run():\n    return None\n")
    (tmp_path / "web" / "client.ts").write_text("export const enabled = false;\n")
    commit_all(tmp_path, "Add base application")
    run_git(tmp_path, "checkout", "--quiet", "-b", "feature/change-model")

    (tmp_path / "src" / "service.py").write_text(
        "import logging\n\ndef run():\n    logging.info('running')\n    return True\n"
    )
    (tmp_path / "web" / "client.ts").write_text(
        "import { api } from './api';\n"
        "export async function loadClient() {\n"
        "  return api.get('/client');\n"
        "}\n"
    )
    (tmp_path / "tests" / "test_service.py").write_text(
        "from src.service import run\n\ndef test_run():\n    assert run() is True\n"
    )
    commit_all(tmp_path, "Build change model fixture")

    git = GitAdapter(tmp_path)
    repository = git.detect_repository()
    diff = DiffCollector(git, file_filter=FileFilter()).collect(repository, "main")

    context = ChangeModelBuilder(source_reader=git).build(repository, "main", diff)

    assert {symbol.name for symbol in context.changed_symbols} >= {
        "run",
        "loadClient",
        "test_run",
    }
    assert context.related_tests == (Path("tests/test_service.py"),)
    assert {reference.name for reference in context.call_sites} >= {
        "logging.info",
        "api.get",
        "run",
    }


def test_reads_committed_snapshot_when_worktree_is_dirty(tmp_path: Path) -> None:
    run_git(tmp_path, "init", "--quiet")
    run_git(tmp_path, "checkout", "--quiet", "-b", "main")
    run_git(tmp_path, "config", "user.name", "CodeRecall Tests")
    run_git(tmp_path, "config", "user.email", "tests@coderecall.local")
    source_path = tmp_path / "service.py"
    source_path.write_text("def base():\n    return old_call()\n")
    commit_all(tmp_path, "Add base service")
    run_git(tmp_path, "checkout", "--quiet", "-b", "feature/snapshot")
    source_path.write_text("def committed():\n    return target_call()\n")
    commit_all(tmp_path, "Change committed service")
    source_path.write_text("def dirty():\n    return dirty_call()\n")

    git = GitAdapter(tmp_path)
    repository = git.detect_repository()
    diff = DiffCollector(git, file_filter=FileFilter()).collect(repository, "main")

    context = ChangeModelBuilder(source_reader=git).build(repository, "main", diff)

    assert [symbol.name for symbol in context.changed_symbols] == ["committed"]
    assert [reference.name for reference in context.call_sites] == ["target_call"]
