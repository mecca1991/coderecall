"""Integration tests for creating starter project configuration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from coderecall.cli.app import app

runner = CliRunner()

STARTER_CONFIG = """base: main
report_path: coderecall-report.md
questions: 3
include_uncommitted: false
exclude:
  - node_modules/**
  - dist/**
  - build/**
  - vendor/**
"""


def initialize_repository(directory: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=directory, check=True)
    subprocess.run(
        ["git", "checkout", "--quiet", "-b", "main"],
        cwd=directory,
        check=True,
    )


def test_init_writes_exact_starter_at_repository_root_from_nested_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_repository(tmp_path)
    nested = tmp_path / "src" / "package"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["init"])

    target = tmp_path / ".coderecall.yml"
    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == STARTER_CONFIG
    assert f'Created CodeRecall configuration: "{target}"' in result.output
    assert not (nested / ".coderecall.yml").exists()


def test_init_explicit_relative_path_creates_missing_parent_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_repository(tmp_path)
    nested = tmp_path / "workspace"
    nested.mkdir()
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["init", "--path", "config/coderecall.yml"])

    target = nested / "config" / "coderecall.yml"
    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == STARTER_CONFIG


def test_init_refuses_to_overwrite_and_preserves_existing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_repository(tmp_path)
    target = tmp_path / ".coderecall.yml"
    target.write_text("existing: content\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert target.read_text(encoding="utf-8") == "existing: content\n"
    assert f'CodeRecall will not overwrite existing path "{target}"' in result.output
    assert "Choose a different `--path`" in result.output


def test_init_refuses_dangling_symlink_without_creating_its_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_repository(tmp_path)
    target = tmp_path / ".coderecall.yml"
    symlink_destination = tmp_path / "missing-config.yml"
    target.symlink_to(symlink_destination)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert target.is_symlink()
    assert not symlink_destination.exists()
    assert f'CodeRecall will not overwrite existing path "{target}"' in result.output


def test_init_reports_parent_creation_failure_as_actionable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_repository(tmp_path)
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init", "--path", "blocked/config.yml"])

    assert result.exit_code == 1
    assert f'Could not prepare the starter config path "{blocked / "config.yml"}"' in result.output
    assert "Choose a writable `--path`" in result.output


def test_init_requires_a_git_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "could not find a Git repository" in result.output
    assert not (tmp_path / ".coderecall.yml").exists()


def test_init_has_no_force_option() -> None:
    result = runner.invoke(app, ["init", "--help"])

    assert result.exit_code == 0
    assert "--path" in result.output
    assert "--force" not in result.output
