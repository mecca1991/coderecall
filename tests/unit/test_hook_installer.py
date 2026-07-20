"""Tests for generating and safely installing the advisory pre-push hook."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from coderecall.core.errors import HookInstallationFailed
from coderecall.hooks import (
    HOOK_OWNERSHIP_MARKER,
    HOOK_VERSION_MARKER,
    HookInstallationStatus,
    HookInstaller,
    build_pre_push_hook,
)

EXPECTED_AUTOMATIC_HOOK = r"""#!/bin/sh
# Managed by CodeRecall. Do not edit.
# CodeRecall hook version: 1
# Advisory only. Bypass entirely with: git push --no-verify

if ! (: </dev/tty) 2>/dev/null; then
    printf '%s\n' 'CodeRecall: no interactive terminal available; continuing push.' >&2
    exit 0
fi

printf '%s' 'Run CodeRecall review before push? [y/N] ' >/dev/tty
IFS= read -r coderecall_answer </dev/tty || coderecall_answer=

case "$coderecall_answer" in
    [Yy]|[Yy][Ee][Ss])
        ;;
    *)
        printf '%s\n' 'CodeRecall: review skipped; continuing push.' >/dev/tty
        exit 0
        ;;
esac

if coderecall review </dev/tty; then
    printf '%s\n' 'CodeRecall: review completed; continuing push.' >/dev/tty
else
    coderecall_status=$?
    printf 'CodeRecall: review exited with status %s; continuing push.\n' \
        "$coderecall_status" >/dev/tty
fi

exit 0
"""


def test_build_pre_push_hook_has_exact_managed_automatic_content() -> None:
    hook = build_pre_push_hook(None)

    assert hook == EXPECTED_AUTOMATIC_HOOK
    assert HOOK_OWNERSHIP_MARKER in hook
    assert HOOK_VERSION_MARKER in hook
    assert "--base" not in hook
    assert "git push --no-verify" in hook


def test_build_pre_push_hook_shell_quotes_explicit_base() -> None:
    hook = build_pre_push_hook("release candidate's branch")

    assert "coderecall review --base 'release candidate'\"'\"'s branch' </dev/tty" in hook


def test_installer_creates_hook_exclusively_with_executable_permissions(tmp_path: Path) -> None:
    hook_path = tmp_path / "hooks" / "pre-push"
    content = build_pre_push_hook(None)

    result = HookInstaller().install(hook_path, content, force=False)

    assert result.path == hook_path
    assert result.status is HookInstallationStatus.INSTALLED
    assert hook_path.read_text(encoding="utf-8") == content
    assert hook_path.stat().st_mode & 0o111 == 0o111


@pytest.mark.parametrize("executable_mode", [0o700, 0o755])
def test_installer_treats_identical_executable_hook_as_already_current(
    tmp_path: Path,
    executable_mode: int,
) -> None:
    hook_path = tmp_path / "pre-push"
    content = build_pre_push_hook(None)
    hook_path.write_text(content, encoding="utf-8")
    hook_path.chmod(executable_mode)
    original_stat = hook_path.stat()

    result = HookInstaller().install(hook_path, content, force=False)

    assert result.status is HookInstallationStatus.ALREADY_CURRENT
    assert hook_path.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert hook_path.stat().st_ino == original_stat.st_ino


def test_installer_restores_execute_permissions_on_identical_managed_hook(
    tmp_path: Path,
) -> None:
    hook_path = tmp_path / "pre-push"
    content = build_pre_push_hook(None)
    hook_path.write_text(content, encoding="utf-8")
    hook_path.chmod(0o644)

    result = HookInstaller().install(hook_path, content, force=False)

    assert result.status is HookInstallationStatus.UPDATED
    assert hook_path.stat().st_mode & 0o111 == 0o111


def test_installer_requires_force_for_changed_managed_hook(tmp_path: Path) -> None:
    hook_path = tmp_path / "pre-push"
    previous = build_pre_push_hook("main")
    replacement = build_pre_push_hook("release")
    hook_path.write_text(previous, encoding="utf-8")
    hook_path.chmod(0o755)

    with pytest.raises(HookInstallationFailed) as captured:
        HookInstaller().install(hook_path, replacement, force=False)

    assert "CodeRecall-managed pre-push hook has different content" in captured.value.message
    assert "install-hook --force" in (captured.value.recovery or "")
    assert hook_path.read_text(encoding="utf-8") == previous


def test_installer_force_updates_changed_managed_hook_atomically(tmp_path: Path) -> None:
    hook_path = tmp_path / "pre-push"
    previous = build_pre_push_hook("main")
    replacement = build_pre_push_hook("release")
    hook_path.write_text(previous, encoding="utf-8")
    hook_path.chmod(0o755)
    previous_inode = hook_path.stat().st_ino

    result = HookInstaller().install(hook_path, replacement, force=True)

    assert result.status is HookInstallationStatus.UPDATED
    assert hook_path.read_text(encoding="utf-8") == replacement
    assert hook_path.stat().st_ino != previous_inode
    assert hook_path.stat().st_mode & 0o111 == 0o111
    assert list(tmp_path.glob(".pre-push.coderecall-*")) == []


def test_installer_preserves_unmanaged_file_even_with_force(tmp_path: Path) -> None:
    hook_path = tmp_path / "pre-push"
    unmanaged = "#!/bin/sh\necho existing\n"
    hook_path.write_text(unmanaged, encoding="utf-8")

    with pytest.raises(HookInstallationFailed) as captured:
        HookInstaller().install(hook_path, build_pre_push_hook(None), force=True)

    assert "will not overwrite existing pre-push hook" in captured.value.message
    assert "Integrate CodeRecall manually" in (captured.value.recovery or "")
    assert "`--force` only replaces CodeRecall-managed hooks" in (captured.value.recovery or "")
    assert hook_path.read_text(encoding="utf-8") == unmanaged


def test_installer_preserves_symlink_even_when_target_is_managed_and_force_is_set(
    tmp_path: Path,
) -> None:
    target = tmp_path / "managed-target"
    target.write_text(build_pre_push_hook(None), encoding="utf-8")
    hook_path = tmp_path / "pre-push"
    hook_path.symlink_to(target)

    with pytest.raises(HookInstallationFailed) as captured:
        HookInstaller().install(hook_path, build_pre_push_hook("main"), force=True)

    assert "symbolic link" in captured.value.message
    assert hook_path.is_symlink()
    assert target.read_text(encoding="utf-8") == build_pre_push_hook(None)


def test_installer_keeps_existing_hook_when_atomic_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hook_path = tmp_path / "pre-push"
    previous = build_pre_push_hook("main")
    hook_path.write_text(previous, encoding="utf-8")

    def fail_replace(source: str | Path, destination: str | Path) -> None:
        assert Path(source).parent == hook_path.parent
        assert Path(destination) == hook_path
        raise OSError("replace denied")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(HookInstallationFailed) as captured:
        HookInstaller().install(hook_path, build_pre_push_hook("release"), force=True)

    assert "Could not update" in captured.value.message
    assert hook_path.read_text(encoding="utf-8") == previous
    assert list(tmp_path.glob(".pre-push.coderecall-*")) == []


def test_installer_reports_parent_filesystem_failure(tmp_path: Path) -> None:
    blocked_parent = tmp_path / "hooks"
    blocked_parent.write_text("not a directory", encoding="utf-8")
    hook_path = blocked_parent / "pre-push"

    with pytest.raises(HookInstallationFailed) as captured:
        HookInstaller().install(hook_path, build_pre_push_hook(None), force=False)

    assert "Could not prepare the hook directory" in captured.value.message
    assert str(hook_path.parent) in captured.value.message
