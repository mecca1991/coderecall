"""CLI smoke tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from coderecall.cli.app import app
from coderecall.cli.commands.review import _format_changed_file, _format_filtered_file
from coderecall.core.types import ChangedFile, FileStatus, FilteredFile, FilterReason

runner = CliRunner()


def test_root_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "review" in result.output
    assert "install-hook" in result.output
    assert "init" in result.output


def test_review_help_lists_mvp_options() -> None:
    result = runner.invoke(app, ["review", "--help"])

    assert result.exit_code == 0
    assert "--base" in result.output
    assert "--report" in result.output
    assert "--questions" in result.output
    assert "--no-follow-up" in result.output
    assert "--include-uncommitted" in result.output
    assert "--plain" in result.output


def test_version_option() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "coderecall 0.1.0" in result.output


def test_review_reports_repository_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "checkout", "--quiet", "-b", "main"],
        cwd=tmp_path,
        check=True,
    )
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("first revision\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CodeRecall Tests",
            "-c",
            "user.email=tests@coderecall.local",
            "commit",
            "--quiet",
            "-m",
            "Initial commit",
        ],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", "-b", "feature/cli-context"],
        cwd=tmp_path,
        check=True,
    )
    tracked.write_text("second revision\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CodeRecall Tests",
            "-c",
            "user.email=tests@coderecall.local",
            "commit",
            "--quiet",
            "-m",
            "Feature change",
        ],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["review"])

    assert result.exit_code == 0
    assert "Current branch: feature/cli-context" in result.output
    assert "Repository root:" in result.output
    assert "Base branch: main" in result.output
    assert "Changed files: 1" in result.output
    assert 'modified: "tracked.txt"' in result.output


def test_review_reports_filtered_files_and_reasons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "checkout", "--quiet", "-b", "main"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "app.py").write_text("ENABLED = False\n")
    (tmp_path / "package-lock.json").write_text('{"lockfileVersion": 3}\n')
    subprocess.run(["git", "add", "--all"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CodeRecall Tests",
            "-c",
            "user.email=tests@coderecall.local",
            "commit",
            "--quiet",
            "-m",
            "Initial commit",
        ],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", "-b", "feature/filter-context"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "app.py").write_text("ENABLED = True\n")
    (tmp_path / "package-lock.json").write_text('{"lockfileVersion": 3, "changed": true}\n')
    subprocess.run(["git", "add", "--all"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CodeRecall Tests",
            "-c",
            "user.email=tests@coderecall.local",
            "commit",
            "--quiet",
            "-m",
            "Change application and lockfile",
        ],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["review"])

    assert result.exit_code == 0
    assert "Changed files: 2" in result.output
    assert "Files for analysis: 1" in result.output
    assert 'modified: "app.py"' in result.output
    assert "Filtered files: 1" in result.output
    assert 'modified: "package-lock.json" (filtered: lockfile)' in result.output


def test_review_fails_clearly_outside_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["review"])

    assert result.exit_code == 1
    assert "could not find a Git repository" in result.output
    assert "Run this command inside a Git working tree." in result.output
    assert "git rev-parse --show-toplevel" in result.output


def test_review_reports_missing_base_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "checkout", "--quiet", "-b", "feature/no-base"],
        cwd=tmp_path,
        check=True,
    )
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("first revision\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CodeRecall Tests",
            "-c",
            "user.email=tests@coderecall.local",
            "commit",
            "--quiet",
            "-m",
            "Initial commit",
        ],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["review"])

    assert result.exit_code == 1
    assert "could not infer a base branch" in result.output
    assert "coderecall review --base <branch>" in result.output


def test_review_rejects_explicit_empty_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "checkout", "--quiet", "-b", "main"],
        cwd=tmp_path,
        check=True,
    )
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("first revision\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CodeRecall Tests",
            "-c",
            "user.email=tests@coderecall.local",
            "commit",
            "--quiet",
            "-m",
            "Initial commit",
        ],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["review", "--base", ""])

    assert result.exit_code == 1
    assert "Base branch cannot be empty." in result.output


def test_changed_file_paths_are_escaped_for_terminal_output() -> None:
    changed_file = ChangedFile(
        path=Path("line\nbreak-\x1b[31m.py"),
        status=FileStatus.MODIFIED,
    )

    rendered = _format_changed_file(changed_file)

    assert "\n" not in rendered
    assert "\x1b" not in rendered
    assert "\\n" in rendered
    assert "\\u001b" in rendered


def test_filtered_file_paths_are_escaped_for_terminal_output() -> None:
    filtered_file = FilteredFile(
        path=Path("line\nbreak-\x1b[31m.js"),
        status=FileStatus.MODIFIED,
        reason=FilterReason.MINIFIED_ASSET,
    )

    rendered = _format_filtered_file(filtered_file)

    assert "\n" not in rendered
    assert "\x1b" not in rendered
    assert "\\n" in rendered
    assert "\\u001b" in rendered
