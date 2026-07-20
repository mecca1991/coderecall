"""Runtime tests for the generated advisory POSIX pre-push hook."""

from __future__ import annotations

import fcntl
import os
import pty
import select
import subprocess
import termios
import time
from pathlib import Path

import pytest

from coderecall.hooks import build_pre_push_hook

GIT_REF_INPUT = (
    "refs/heads/feature abcdef refs/heads/feature "
    "0000000000000000000000000000000000000000\n"
)


def write_hook(directory: Path, base: str | None = None) -> Path:
    hook_path = directory / "pre-push"
    hook_path.write_text(build_pre_push_hook(base), encoding="utf-8")
    hook_path.chmod(0o755)
    return hook_path


def write_fake_coderecall(directory: Path) -> Path:
    executable = directory / "coderecall"
    executable.write_text(
        "#!/bin/sh\n"
        ': >"$CODERECALL_LOG"\n'
        'for coderecall_arg do\n'
        '    printf \'arg:%s\\n\' "$coderecall_arg" >>"$CODERECALL_LOG"\n'
        "done\n"
        "IFS= read -r coderecall_review_input\n"
        'printf \'input:%s\\n\' "$coderecall_review_input" >>"$CODERECALL_LOG"\n'
        'exit "${CODERECALL_EXIT:-0}"\n',
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def run_with_controlling_tty(
    hook_path: Path,
    terminal_input: str,
    environment: dict[str, str],
) -> tuple[int, str]:
    master_fd, slave_fd = pty.openpty()

    def establish_controlling_terminal() -> None:
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        os.tcsetpgrp(slave_fd, os.getpgrp())

    process = subprocess.Popen(
        [str(hook_path)],
        stdin=subprocess.PIPE,
        stdout=slave_fd,
        stderr=slave_fd,
        env=environment,
        preexec_fn=establish_controlling_terminal,
    )
    os.close(slave_fd)
    assert process.stdin is not None
    process.stdin.write(GIT_REF_INPUT.encode())
    process.stdin.close()

    output_parts: list[bytes] = []
    input_sent = False
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        readable, _, _ = select.select([master_fd], [], [], 0.05)
        if readable:
            try:
                part = os.read(master_fd, 4096)
            except OSError:
                part = b""
            if part:
                output_parts.append(part)
                if not input_sent and b"Run CodeRecall review before push? [y/N]" in b"".join(
                    output_parts
                ):
                    os.write(master_fd, terminal_input.encode())
                    input_sent = True
        return_code = process.poll()
        if return_code is not None:
            os.close(master_fd)
            return return_code, b"".join(output_parts).decode(errors="replace")

    process.kill()
    process.wait()
    os.close(master_fd)
    output = b"".join(output_parts).decode(errors="replace")
    raise AssertionError(f"hook did not exit; terminal output was: {output!r}")


def fake_environment(tmp_path: Path, *, exit_status: int = 0) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    write_fake_coderecall(fake_bin)
    log_path = tmp_path / "coderecall.log"
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}"
    environment["CODERECALL_LOG"] = str(log_path)
    environment["CODERECALL_EXIT"] = str(exit_status)
    return environment, log_path


@pytest.mark.parametrize("accepted_response", ["y", "YeS"])
def test_hook_accepts_y_or_yes_without_consuming_git_stdin(
    tmp_path: Path,
    accepted_response: str,
) -> None:
    hook_path = write_hook(tmp_path, "release candidate")
    environment, log_path = fake_environment(tmp_path)

    return_code, output = run_with_controlling_tty(
        hook_path,
        f"{accepted_response}\nanswer from tty\n",
        environment,
    )

    assert return_code == 0
    assert "Run CodeRecall review before push? [y/N]" in output
    assert "CodeRecall: review completed; continuing push." in output
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "arg:review",
        "arg:--base",
        "arg:release candidate",
        "input:answer from tty",
    ]


def test_hook_decline_skips_review_and_continues(tmp_path: Path) -> None:
    hook_path = write_hook(tmp_path)
    environment, log_path = fake_environment(tmp_path)

    return_code, output = run_with_controlling_tty(hook_path, "no\n", environment)

    assert return_code == 0
    assert "CodeRecall: review skipped; continuing push." in output
    assert not log_path.exists()


def test_hook_review_failure_reports_status_and_continues(tmp_path: Path) -> None:
    hook_path = write_hook(tmp_path)
    environment, log_path = fake_environment(tmp_path, exit_status=7)

    return_code, output = run_with_controlling_tty(
        hook_path,
        "yes\nreview input\n",
        environment,
    )

    assert return_code == 0
    assert "CodeRecall: review exited with status 7; continuing push." in output
    assert log_path.exists()


def test_hook_missing_coderecall_reports_status_and_continues(tmp_path: Path) -> None:
    hook_path = write_hook(tmp_path)
    environment = os.environ.copy()
    environment["PATH"] = str(tmp_path / "empty-bin")

    return_code, output = run_with_controlling_tty(hook_path, "yes\n", environment)

    assert return_code == 0
    assert "CodeRecall: review exited with status 127; continuing push." in output


def test_hook_without_terminal_skips_review_and_continues(tmp_path: Path) -> None:
    hook_path = write_hook(tmp_path)
    environment, log_path = fake_environment(tmp_path)

    result = subprocess.run(
        [str(hook_path)],
        input=GIT_REF_INPUT,
        capture_output=True,
        check=False,
        encoding="utf-8",
        env=environment,
        start_new_session=True,
    )

    assert result.returncode == 0
    assert "no interactive terminal available; continuing push" in result.stderr
    assert not log_path.exists()
