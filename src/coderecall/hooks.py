"""Generate and safely install CodeRecall's advisory pre-push hook."""

from __future__ import annotations

import os
import shlex
import stat
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from coderecall.core.errors import HookInstallationFailed

HOOK_OWNERSHIP_MARKER = "# Managed by CodeRecall. Do not edit."
HOOK_VERSION_MARKER = "# CodeRecall hook version: 1"


class HookInstallationStatus(Enum):
    """Possible outcomes of a successful hook installation."""

    INSTALLED = "installed"
    ALREADY_CURRENT = "already-current"
    UPDATED = "updated"


@dataclass(frozen=True)
class HookInstallationResult:
    """Describe the installed path and whether it changed."""

    path: Path
    status: HookInstallationStatus


def build_pre_push_hook(base: str | None) -> str:
    """Return the complete POSIX hook, optionally pinned to an explicit base."""

    review_command = "coderecall review"
    if base is not None:
        review_command += f" --base {shlex.quote(base)}"

    return f"""#!/bin/sh
{HOOK_OWNERSHIP_MARKER}
{HOOK_VERSION_MARKER}
# Advisory only. Bypass entirely with: git push --no-verify

if ! (: </dev/tty) 2>/dev/null; then
    printf '%s\\n' 'CodeRecall: no interactive terminal available; continuing push.' >&2
    exit 0
fi

printf '%s' 'Run CodeRecall review before push? [y/N] ' >/dev/tty
IFS= read -r coderecall_answer </dev/tty || coderecall_answer=

case "$coderecall_answer" in
    [Yy]|[Yy][Ee][Ss])
        ;;
    *)
        printf '%s\\n' 'CodeRecall: review skipped; continuing push.' >/dev/tty
        exit 0
        ;;
esac

if {review_command} </dev/tty; then
    printf '%s\\n' 'CodeRecall: review completed; continuing push.' >/dev/tty
else
    coderecall_status=$?
    printf 'CodeRecall: review exited with status %s; continuing push.\\n' \\
        "$coderecall_status" >/dev/tty
fi

exit 0
"""


class HookInstaller:
    """Install only absent or recognizably CodeRecall-managed hook files."""

    def install(self, path: Path, content: str, *, force: bool) -> HookInstallationResult:
        """Install content without following or replacing unmanaged hook paths."""

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise HookInstallationFailed(
                f'Could not prepare the hook directory "{path.parent}": {error}',
                recovery="Fix the directory permissions or choose a writable `core.hooksPath`.",
            ) from error

        try:
            existing_stat = path.lstat()
        except FileNotFoundError:
            return self._create(path, content)
        except OSError as error:
            raise self._filesystem_error("inspect", path, error) from error

        return self._update_existing(path, content, force=force, existing_stat=existing_stat)

    def _create(self, path: Path, content: str) -> HookInstallationResult:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor: int | None = None
        created_stat: os.stat_result | None = None
        try:
            descriptor = os.open(path, flags, 0o755)
            created_stat = os.fstat(descriptor)
            os.fchmod(descriptor, 0o755)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as hook_file:
                descriptor = None
                hook_file.write(content)
                hook_file.flush()
                os.fsync(hook_file.fileno())
        except FileExistsError:
            existing_stat = path.lstat()
            return self._update_existing(
                path,
                content,
                force=False,
                existing_stat=existing_stat,
            )
        except OSError as error:
            if descriptor is not None:
                os.close(descriptor)
            if created_stat is not None:
                self._unlink_if_same_file(path, created_stat)
            raise HookInstallationFailed(
                f'Could not install the pre-push hook at "{path}": {error}',
                recovery="Fix the hook path permissions and run `coderecall install-hook` again.",
            ) from error

        return HookInstallationResult(path=path, status=HookInstallationStatus.INSTALLED)

    def _update_existing(
        self,
        path: Path,
        content: str,
        *,
        force: bool,
        existing_stat: os.stat_result,
    ) -> HookInstallationResult:
        if stat.S_ISLNK(existing_stat.st_mode):
            raise HookInstallationFailed(
                f'CodeRecall will not replace symbolic link "{path}".',
                recovery=(
                    "Integrate CodeRecall manually into the linked hook if appropriate. "
                    "`--force` never replaces symbolic links."
                ),
            )
        if not stat.S_ISREG(existing_stat.st_mode):
            raise HookInstallationFailed(
                f'CodeRecall will not overwrite existing pre-push path "{path}".',
                recovery=(
                    "Move the existing path or choose a different `core.hooksPath`. "
                    "`--force` only replaces CodeRecall-managed regular files."
                ),
            )

        existing_content = self._read_without_following(path)
        if not self._is_managed(existing_content):
            raise HookInstallationFailed(
                f'CodeRecall will not overwrite existing pre-push hook at "{path}".',
                recovery=(
                    "Integrate CodeRecall manually into the existing hook. "
                    "`--force` only replaces CodeRecall-managed hooks."
                ),
            )

        if existing_content == content:
            if existing_stat.st_mode & 0o111:
                return HookInstallationResult(
                    path=path,
                    status=HookInstallationStatus.ALREADY_CURRENT,
                )
            self._make_executable_without_following(path)
            return HookInstallationResult(path=path, status=HookInstallationStatus.UPDATED)

        if not force:
            raise HookInstallationFailed(
                f'The CodeRecall-managed pre-push hook has different content at "{path}".',
                recovery=(
                    "Review the existing hook, then run `coderecall install-hook --force` "
                    "to replace it."
                ),
            )

        self._replace_atomically(path, content, existing_stat)
        return HookInstallationResult(path=path, status=HookInstallationStatus.UPDATED)

    @staticmethod
    def _is_managed(content: str) -> bool:
        lines = content.splitlines()
        return len(lines) >= 2 and lines[0] == "#!/bin/sh" and lines[1] == HOOK_OWNERSHIP_MARKER

    @staticmethod
    def _read_without_following(path: Path) -> str:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "r", encoding="utf-8", errors="surrogateescape") as hook:
                if not stat.S_ISREG(os.fstat(hook.fileno()).st_mode):
                    raise HookInstallationFailed(
                        f'CodeRecall will not overwrite existing pre-push path "{path}".',
                        recovery="Move the existing path or integrate CodeRecall manually.",
                    )
                return hook.read()
        except HookInstallationFailed:
            raise
        except OSError as error:
            raise HookInstallationFailed(
                f'Could not read the existing pre-push hook at "{path}": {error}',
                recovery="Check the hook permissions and integrate CodeRecall manually.",
            ) from error

    @staticmethod
    def _make_executable_without_following(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
            try:
                file_stat = os.fstat(descriptor)
                if not stat.S_ISREG(file_stat.st_mode):
                    raise OSError("hook path is no longer a regular file")
                os.fchmod(descriptor, stat.S_IMODE(file_stat.st_mode) | 0o111)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise HookInstallationFailed(
                f'Could not make the pre-push hook executable at "{path}": {error}',
                recovery="Fix the hook permissions and run `coderecall install-hook` again.",
            ) from error

    @staticmethod
    def _replace_atomically(path: Path, content: str, existing_stat: os.stat_result) -> None:
        descriptor: int | None = None
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".pre-push.coderecall-",
                dir=path.parent,
            )
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, 0o755)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as hook_file:
                descriptor = None
                hook_file.write(content)
                hook_file.flush()
                os.fsync(hook_file.fileno())

            current_stat = path.lstat()
            if (
                not stat.S_ISREG(current_stat.st_mode)
                or current_stat.st_dev != existing_stat.st_dev
                or current_stat.st_ino != existing_stat.st_ino
            ):
                raise OSError("hook path changed during installation")
            os.replace(temporary_path, path)
        except OSError as error:
            raise HookInstallationFailed(
                f'Could not update the pre-push hook at "{path}": {error}',
                recovery="The existing hook was preserved; check permissions and try again.",
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _unlink_if_same_file(path: Path, expected: os.stat_result) -> None:
        try:
            current = path.lstat()
            if current.st_dev == expected.st_dev and current.st_ino == expected.st_ino:
                path.unlink()
        except OSError:
            pass

    @staticmethod
    def _filesystem_error(action: str, path: Path, error: OSError) -> HookInstallationFailed:
        return HookInstallationFailed(
            f'Could not {action} the pre-push hook at "{path}": {error}',
            recovery="Check the hook path and permissions, then run the installer again.",
        )
