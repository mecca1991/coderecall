"""Integration tests for the opt-in pre-push hook installer command."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from coderecall.cli.app import app
from coderecall.hooks import build_pre_push_hook

runner = CliRunner()


def run_git(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=directory,
        capture_output=True,
        check=True,
        text=True,
    )


def initialize_repository(directory: Path) -> None:
    run_git(directory, "init", "--quiet")
    run_git(directory, "checkout", "--quiet", "-b", "main")
    tracked = directory / "tracked.txt"
    tracked.write_text("initial\n", encoding="utf-8")
    run_git(directory, "add", "tracked.txt")
    run_git(
        directory,
        "-c",
        "user.name=CodeRecall Tests",
        "-c",
        "user.email=tests@coderecall.local",
        "commit",
        "--quiet",
        "-m",
        "Initial commit",
    )
    run_git(directory, "branch", "release")
    run_git(directory, "checkout", "--quiet", "-b", "feature/hook")


def resolved_hook_path(directory: Path) -> Path:
    result = run_git(
        directory,
        "rev-parse",
        "--path-format=absolute",
        "--git-path",
        "hooks/pre-push",
    )
    return Path(result.stdout.strip())


def test_install_hook_help_discloses_bypass_and_safety() -> None:
    result = runner.invoke(app, ["install-hook", "--help"], terminal_width=140)

    assert result.exit_code == 0
    assert "--base" in result.output
    assert "--force" in result.output
    assert "git push --no-verify" in result.output
    assert "unmanaged" in result.output.lower()


def test_install_hook_from_nested_directory_uses_automatic_runtime_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_repository(tmp_path)
    (tmp_path / ".coderecall.yml").write_text("base: release\n", encoding="utf-8")
    nested = tmp_path / "src" / "package"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["install-hook"])

    hook_path = resolved_hook_path(tmp_path)
    assert result.exit_code == 0
    assert f'Hook path: "{hook_path}"' in result.output
    assert (
        "Review base: automatic (runtime configuration, then main/master inference)"
        in result.output
    )
    assert (
        "Advisory: declining, no terminal, or review failure continues the push." in result.output
    )
    assert "Bypass: git push --no-verify" in result.output
    assert f'Installed CodeRecall pre-push hook: "{hook_path}"' in result.output
    assert hook_path.read_text(encoding="utf-8") == build_pre_push_hook(None)
    assert "--base" not in hook_path.read_text(encoding="utf-8")


def test_install_hook_validates_and_stores_explicit_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_repository(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["install-hook", "--base", "release"])

    hook_path = resolved_hook_path(tmp_path)
    assert result.exit_code == 0
    assert "Review base: release (explicit)" in result.output
    assert "coderecall review --base release </dev/tty" in hook_path.read_text(encoding="utf-8")


def test_install_hook_rejects_missing_explicit_base_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_repository(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["install-hook", "--base", "missing"])

    assert result.exit_code == 1
    assert "Could not find base branch `missing`" in result.output
    assert not resolved_hook_path(tmp_path).exists()


def test_install_hook_is_idempotent_and_force_updates_managed_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_repository(tmp_path)
    monkeypatch.chdir(tmp_path)

    installed = runner.invoke(app, ["install-hook", "--base", "main"])
    current = runner.invoke(app, ["install-hook", "--base", "main"])
    blocked = runner.invoke(app, ["install-hook", "--base", "release"])
    updated = runner.invoke(app, ["install-hook", "--base", "release", "--force"])

    assert installed.exit_code == 0
    assert current.exit_code == 0
    assert "already current" in current.output
    assert blocked.exit_code == 1
    assert "install-hook --force" in blocked.output
    assert updated.exit_code == 0
    assert "Updated CodeRecall pre-push hook" in updated.output
    assert resolved_hook_path(tmp_path).read_text(encoding="utf-8") == build_pre_push_hook(
        "release"
    )


def test_install_hook_honors_custom_hooks_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_repository(tmp_path)
    run_git(tmp_path, "config", "core.hooksPath", ".custom-hooks")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["install-hook"])

    custom_hook = tmp_path / ".custom-hooks" / "pre-push"
    assert result.exit_code == 0
    assert custom_hook.is_file()
    assert f'Hook path: "{custom_hook}"' in result.output
    assert not (tmp_path / ".git" / "hooks" / "pre-push").exists()


def test_install_hook_rejects_disabled_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_repository(tmp_path)
    run_git(tmp_path, "config", "core.hooksPath", "/dev/null")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["install-hook"])

    assert result.exit_code == 1
    assert "hooks are disabled" in result.output
    assert "core.hooksPath" in result.output


def test_install_hook_preserves_unmanaged_hook_even_with_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_repository(tmp_path)
    hook_path = resolved_hook_path(tmp_path)
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    unmanaged = "#!/bin/sh\nprintf 'team hook\\n'\n"
    hook_path.write_text(unmanaged, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["install-hook", "--force"])

    assert result.exit_code == 1
    assert "will not overwrite existing pre-push hook" in result.output
    assert "Integrate CodeRecall manually" in result.output
    assert hook_path.read_text(encoding="utf-8") == unmanaged


def test_install_hook_reports_non_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["install-hook"])

    assert result.exit_code == 1
    assert "could not find a Git repository" in result.output
    assert "Run this command inside a Git working tree" in result.output


def test_install_hook_discloses_behavior_before_filesystem_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_repository(tmp_path)
    blocked = tmp_path / "blocked-hooks"
    blocked.mkdir()
    blocked.chmod(0o500)
    run_git(tmp_path, "config", "core.hooksPath", str(blocked))
    monkeypatch.chdir(tmp_path)

    try:
        result = runner.invoke(app, ["install-hook"])
    finally:
        blocked.chmod(0o700)

    assert result.exit_code == 1
    assert result.output.index("Hook path:") < result.output.index(
        "Could not install the pre-push hook"
    )
    assert "Bypass: git push --no-verify" in result.output
