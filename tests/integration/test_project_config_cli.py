"""CLI coverage for repository-local project configuration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from coderecall.cli.app import app

runner = CliRunner()


def git(directory: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=directory, check=True)


def create_config_repository(directory: Path) -> None:
    git(directory, "init", "--quiet")
    git(directory, "checkout", "--quiet", "-b", "stable")
    git(directory, "config", "user.name", "CodeRecall Tests")
    git(directory, "config", "user.email", "tests@coderecall.local")
    (directory / "app.py").write_text('STATE = "pending"\n', encoding="utf-8")
    (directory / "worktree.py").write_text('STATE = "clean"\n', encoding="utf-8")
    snapshots = directory / "snapshots"
    snapshots.mkdir()
    (snapshots / "output.txt").write_text("before\n", encoding="utf-8")
    git(directory, "add", "--all")
    git(directory, "commit", "--quiet", "-m", "Initial state")
    git(directory, "checkout", "--quiet", "-b", "feature/config")
    (directory / "app.py").write_text('STATE = "complete"\n', encoding="utf-8")
    (snapshots / "output.txt").write_text("after\n", encoding="utf-8")
    git(directory, "add", "--all")
    git(directory, "commit", "--quiet", "-m", "Feature change")
    (directory / "worktree.py").write_text('STATE = "dirty"\n', encoding="utf-8")


def test_review_uses_every_project_config_field_from_nested_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_config_repository(tmp_path)
    (tmp_path / ".coderecall.yml").write_text(
        """base: stable
report_path: reports/project.md
questions: 1
include_uncommitted: true
exclude:
  - snapshots/**
""",
        encoding="utf-8",
    )
    nested = tmp_path / "src" / "package"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["review", "--no-follow-up", "--plain"], input="\n")

    assert result.exit_code == 0
    assert "Branch: feature/config -> stable" in result.output
    assert "Changes: 3 total, 2 analyzed, 1 filtered" in result.output
    assert 'modified: "snapshots/output.txt" (filtered: configured exclusion)' in result.output
    assert "Question 1/1" in result.output
    assert (tmp_path / "reports" / "project.md").is_file()
    assert not (nested / "coderecall-report.md").exists()


def test_explicit_cli_options_override_config_and_keep_custom_exclusions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_config_repository(tmp_path)
    (tmp_path / ".coderecall.yml").write_text(
        """base: missing
report_path: reports/configured.md
questions: 3
include_uncommitted: true
exclude:
  - snapshots/**
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "review",
            "--base",
            "stable",
            "--report",
            "reports/cli.md",
            "--questions",
            "1",
            "--no-include-uncommitted",
            "--no-follow-up",
            "--plain",
        ],
        input="\n",
    )

    assert result.exit_code == 0
    assert "Branch: feature/config -> stable" in result.output
    assert "Changes: 2 total, 1 analyzed, 1 filtered" in result.output
    assert 'modified: "snapshots/output.txt" (filtered: configured exclusion)' in result.output
    assert "Question 1/1" in result.output
    assert (tmp_path / "reports" / "cli.md").is_file()
    assert not (tmp_path / "reports" / "configured.md").exists()


def test_missing_project_config_is_silent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_config_repository(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["review", "--base", "stable", "--questions", "1", "--plain"],
        input="\n",
    )

    assert result.exit_code == 0
    assert ".coderecall.yml" not in result.output
    assert "configuration" not in result.output.lower()


def test_invalid_project_config_exits_before_review_or_report_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_config_repository(tmp_path)
    config_path = tmp_path / ".coderecall.yml"
    config_path.write_text("questions: 9\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["review", "--base", "stable", "--plain"])

    assert result.exit_code == 1
    assert f'Invalid CodeRecall configuration at "{config_path}"' in result.output
    assert "`questions` must be an integer from 1 to 3" in result.output
    assert "Correct the configuration value" in result.output
    assert "CodeRecall review" not in result.output
    assert not (tmp_path / "coderecall-report.md").exists()


def test_review_help_lists_paired_include_uncommitted_options() -> None:
    result = runner.invoke(app, ["review", "--help"], terminal_width=140)

    assert result.exit_code == 0
    assert "--include-uncomm" in result.output
    assert "--no-include-un" in result.output
