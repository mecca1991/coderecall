"""Tests for Git repository context detection."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from coderecall.core.errors import (
    BaseBranchNotFound,
    DetachedHead,
    DiffCollectionFailed,
    GitCommandFailed,
    HookInstallationFailed,
    NotGitRepository,
)
from coderecall.git import GitAdapter


def run_git(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=directory,
        capture_output=True,
        check=True,
        text=True,
    )


def initialize_repository(directory: Path, branch: str = "feature/context") -> None:
    run_git(directory, "init", "--quiet")
    run_git(directory, "checkout", "--quiet", "-b", branch)


def commit_file(directory: Path, name: str = "tracked.txt") -> None:
    tracked_file = directory / name
    tracked_file.write_text("first revision\n")
    run_git(directory, "add", name)
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


def test_detect_repository_from_nested_directory(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    nested = tmp_path / "src" / "package"
    nested.mkdir(parents=True)

    context = GitAdapter(nested).detect_repository()

    assert context.root == tmp_path.resolve()
    assert context.current_branch == "feature/context"


def test_detect_repository_rejects_non_repository(tmp_path: Path) -> None:
    with pytest.raises(NotGitRepository) as captured:
        GitAdapter(tmp_path).detect_repository()

    assert str(tmp_path) in captured.value.message
    assert captured.value.recovery == "Run this command inside a Git working tree."
    assert "git rev-parse --show-toplevel" in (captured.value.debug_details or "")


def test_detect_repository_uses_stable_git_locale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_git = tmp_path / "fake-git"
    fake_git.write_text(
        "#!/bin/sh\n"
        'if [ "$LC_ALL" = "C" ] && [ -z "$LANGUAGE" ]; then\n'
        '  echo "fatal: not a git repository" >&2\n'
        "else\n"
        '  echo "fatal: pas un depot git" >&2\n'
        "fi\n"
        "exit 128\n"
    )
    fake_git.chmod(0o755)
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
    monkeypatch.setenv("LANGUAGE", "fr")

    with pytest.raises(NotGitRepository) as captured:
        GitAdapter(tmp_path, executable=str(fake_git)).detect_repository()

    assert "not a git repository" in (captured.value.debug_details or "")


def test_detect_repository_rejects_detached_head(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    commit_file(tmp_path)
    run_git(tmp_path, "checkout", "--quiet", "--detach", "HEAD")

    with pytest.raises(DetachedHead) as captured:
        GitAdapter(tmp_path).detect_repository()

    assert "HEAD is detached" in captured.value.message
    assert "Check out the branch" in (captured.value.recovery or "")


def test_detect_repository_reports_missing_git_executable(tmp_path: Path) -> None:
    with pytest.raises(GitCommandFailed) as captured:
        GitAdapter(tmp_path, executable="missing-coderecall-git").detect_repository()

    assert "Git executable" in captured.value.message
    assert captured.value.recovery == "Install Git and ensure it is available on PATH."


def test_select_base_branch_prefers_explicit_ref(tmp_path: Path) -> None:
    initialize_repository(tmp_path, "main")
    commit_file(tmp_path)
    run_git(tmp_path, "branch", "release")
    run_git(tmp_path, "checkout", "--quiet", "-b", "feature/context")
    git = GitAdapter(tmp_path)
    repository = git.detect_repository()

    selected = git.select_base_branch(repository, "release")

    assert selected == "release"


def test_select_base_branch_infers_main_before_master(tmp_path: Path) -> None:
    initialize_repository(tmp_path, "main")
    commit_file(tmp_path)
    run_git(tmp_path, "branch", "master")
    run_git(tmp_path, "checkout", "--quiet", "-b", "feature/context")
    git = GitAdapter(tmp_path)

    selected = git.select_base_branch(git.detect_repository())

    assert selected == "main"


def test_select_base_branch_falls_back_to_master(tmp_path: Path) -> None:
    initialize_repository(tmp_path, "master")
    commit_file(tmp_path)
    run_git(tmp_path, "checkout", "--quiet", "-b", "feature/context")
    git = GitAdapter(tmp_path)

    selected = git.select_base_branch(git.detect_repository())

    assert selected == "master"


def test_select_base_branch_rejects_missing_explicit_ref(tmp_path: Path) -> None:
    initialize_repository(tmp_path, "feature/context")
    commit_file(tmp_path)
    git = GitAdapter(tmp_path)

    with pytest.raises(BaseBranchNotFound) as captured:
        git.select_base_branch(git.detect_repository(), "missing")

    assert captured.value.message == "Could not find base branch `missing`."
    assert "--base <branch>" in (captured.value.recovery or "")


def test_select_base_branch_rejects_explicit_empty_ref(tmp_path: Path) -> None:
    initialize_repository(tmp_path, "main")
    commit_file(tmp_path)
    run_git(tmp_path, "checkout", "--quiet", "-b", "feature/context")
    git = GitAdapter(tmp_path)

    with pytest.raises(BaseBranchNotFound) as captured:
        git.select_base_branch(git.detect_repository(), "")

    assert captured.value.message == "Base branch cannot be empty."
    assert "--base <branch>" in (captured.value.recovery or "")


def test_select_base_branch_fails_when_inference_is_impossible(tmp_path: Path) -> None:
    initialize_repository(tmp_path, "feature/context")
    commit_file(tmp_path)
    git = GitAdapter(tmp_path)

    with pytest.raises(BaseBranchNotFound) as captured:
        git.select_base_branch(git.detect_repository())

    assert "could not infer a base branch" in captured.value.message
    assert "main, master" in (captured.value.debug_details or "")


def test_resolve_pre_push_hook_path_uses_git_default_from_nested_directory(
    tmp_path: Path,
) -> None:
    initialize_repository(tmp_path, "main")
    nested = tmp_path / "src" / "package"
    nested.mkdir(parents=True)
    git = GitAdapter(nested)

    hook_path = git.resolve_pre_push_hook_path(git.detect_repository())

    assert hook_path == (tmp_path / ".git" / "hooks" / "pre-push").resolve()


def test_resolve_pre_push_hook_path_honors_relative_core_hooks_path(tmp_path: Path) -> None:
    initialize_repository(tmp_path, "main")
    run_git(tmp_path, "config", "core.hooksPath", ".githooks")
    git = GitAdapter(tmp_path)

    hook_path = git.resolve_pre_push_hook_path(git.detect_repository())

    assert hook_path == (tmp_path / ".githooks" / "pre-push").resolve()


def test_resolve_pre_push_hook_path_honors_absolute_core_hooks_path(tmp_path: Path) -> None:
    initialize_repository(tmp_path, "main")
    custom_hooks = tmp_path / "shared-hooks"
    run_git(tmp_path, "config", "core.hooksPath", str(custom_hooks))
    git = GitAdapter(tmp_path)

    hook_path = git.resolve_pre_push_hook_path(git.detect_repository())

    assert hook_path == custom_hooks / "pre-push"


def test_resolve_pre_push_hook_path_uses_common_hooks_for_linked_worktree(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    initialize_repository(repository_root, "main")
    commit_file(repository_root)
    linked_worktree = tmp_path / "linked"
    run_git(
        repository_root,
        "worktree",
        "add",
        "--quiet",
        "-b",
        "feature/hook",
        str(linked_worktree),
    )
    git = GitAdapter(linked_worktree)

    hook_path = git.resolve_pre_push_hook_path(git.detect_repository())

    assert hook_path == (repository_root / ".git" / "hooks" / "pre-push").resolve()


def test_resolve_pre_push_hook_path_rejects_disabled_hooks(tmp_path: Path) -> None:
    initialize_repository(tmp_path, "main")
    run_git(tmp_path, "config", "core.hooksPath", "/dev/null")
    git = GitAdapter(tmp_path)

    with pytest.raises(HookInstallationFailed) as captured:
        git.resolve_pre_push_hook_path(git.detect_repository())

    assert "hooks are disabled" in captured.value.message
    assert "core.hooksPath" in (captured.value.recovery or "")


def test_find_merge_base_reports_unrelated_histories(tmp_path: Path) -> None:
    initialize_repository(tmp_path, "main")
    commit_file(tmp_path)
    run_git(tmp_path, "checkout", "--quiet", "--orphan", "isolated")
    (tmp_path / "tracked.txt").unlink()
    (tmp_path / "isolated.txt").write_text("unrelated history\n")
    run_git(tmp_path, "add", "--all")
    run_git(
        tmp_path,
        "-c",
        "user.name=CodeRecall Tests",
        "-c",
        "user.email=tests@coderecall.local",
        "commit",
        "--quiet",
        "-m",
        "Isolated commit",
    )
    git = GitAdapter(tmp_path)

    with pytest.raises(DiffCollectionFailed) as captured:
        git.find_merge_base(git.detect_repository(), "main")

    assert "could not find a merge base" in captured.value.message
    assert "shares history" in (captured.value.recovery or "")
