"""CLI smoke tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from coderecall.analysis import QuestionGenerator
from coderecall.cli.app import app
from coderecall.cli.commands.review import (
    _format_changed_file,
    _format_filtered_file,
    _render_diff_summary,
)
from coderecall.core.types import (
    ChangeContext,
    ChangedFile,
    DiffSummary,
    EvidenceCitation,
    FileStatus,
    FilteredFile,
    FilterReason,
    LikelySideEffect,
    Question,
    SideEffectKind,
)

runner = CliRunner()


def create_python_feature_repository(directory: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=directory, check=True)
    subprocess.run(["git", "checkout", "--quiet", "-b", "main"], cwd=directory, check=True)
    subprocess.run(["git", "config", "user.name", "CodeRecall Tests"], cwd=directory, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@coderecall.local"],
        cwd=directory,
        check=True,
    )
    source = directory / "checkout.py"
    source.write_text('def process_order() -> str:\n    return "pending"\n')
    subprocess.run(["git", "add", "checkout.py"], cwd=directory, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "Add checkout flow"], cwd=directory, check=True
    )
    subprocess.run(
        ["git", "checkout", "--quiet", "-b", "feature/complete-order"],
        cwd=directory,
        check=True,
    )
    source.write_text('def process_order() -> str:\n    return "complete"\n')
    subprocess.run(["git", "add", "checkout.py"], cwd=directory, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "Complete processed orders"],
        cwd=directory,
        check=True,
    )


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

    result = runner.invoke(app, ["review"], input="")

    assert result.exit_code == 0
    assert "Current branch: feature/cli-context" in result.output
    assert "Repository root:" in result.output
    assert "Base branch: main" in result.output
    assert "Changed files: 1" in result.output
    assert 'modified: "tracked.txt"' in result.output
    assert "Diff summary" in result.output
    assert "Purpose: Likely updates code in 1 meaningful file." in result.output
    assert result.output.index("Diff summary") < result.output.index("Question 1 of 3 [behavior]")
    assert "Answers: 0 answered, 3 skipped" in result.output


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


def test_diff_summary_renderer_is_concise_and_escapes_paths() -> None:
    unsafe_path = Path("src/line\nbreak-\x1b[31m.py")
    summary = DiffSummary(
        purpose="Likely updates `run` across 2 meaningful files, with a file write signal.",
        relevant_files=(unsafe_path, Path("tests/test_run.py")),
        tests=(Path("tests/test_run.py"),),
        side_effects=(
            LikelySideEffect(
                kind=SideEffectKind.FILE_WRITE,
                description="The change may write to a local file.",
                evidence=(EvidenceCitation(kind="call", file_path=unsafe_path, symbol="open"),),
            ),
        ),
        uncertainty_notes=("Call evidence was incomplete.",),
    )

    rendered = "\n".join(_render_diff_summary(summary))

    assert "Purpose: Likely updates `run`" in rendered
    assert "Relevant files:" in rendered
    assert "Tests found:" in rendered
    assert "Likely side effects:" in rendered
    assert "Uncertainty:" in rendered
    assert "\x1b" not in rendered
    assert "src/line\nbreak" not in rendered
    assert '"src/line\\nbreak-\\u001b[31m.py"' in rendered


def test_review_stops_before_questions_when_every_change_is_filtered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "checkout", "--quiet", "-b", "main"],
        cwd=tmp_path,
        check=True,
    )
    lockfile = tmp_path / "package-lock.json"
    lockfile.write_text('{"lockfileVersion": 3}\n')
    subprocess.run(["git", "add", "package-lock.json"], cwd=tmp_path, check=True)
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
        ["git", "checkout", "--quiet", "-b", "feature/lockfile-only"],
        cwd=tmp_path,
        check=True,
    )
    lockfile.write_text('{"lockfileVersion": 3, "changed": true}\n')
    subprocess.run(["git", "add", "package-lock.json"], cwd=tmp_path, check=True)
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
            "Update lockfile",
        ],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["review", "--plain"])

    assert result.exit_code == 0
    assert "Files for analysis: 0" in result.output
    assert 'modified: "package-lock.json" (filtered: lockfile)' in result.output
    assert "Purpose: No meaningful code changes were available to summarize." in result.output
    assert "Questions:" not in result.output
    assert "Question and report generation are not implemented yet." not in result.output
    assert "A blank line submits" not in result.output


@pytest.mark.parametrize(
    ("question_count", "expected_categories"),
    (
        (1, ("behavior",)),
        (2, ("behavior", "failure")),
        (3, ("behavior", "failure", "evidence")),
    ),
)
def test_review_asks_requested_questions_in_stable_category_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    question_count: int,
    expected_categories: tuple[str, ...],
) -> None:
    create_python_feature_repository(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["review", "--base", "main", "--questions", str(question_count), "--plain"],
        input="\n" * question_count,
    )

    assert result.exit_code == 0
    assert result.output.count("Question ") == question_count
    assert (
        tuple(
            category
            for category in ("behavior", "failure", "evidence")
            if f"[{category}]" in result.output
        )
        == expected_categories
    )
    assert f"Answers: 0 answered, {question_count} skipped" in result.output


def test_review_rejects_more_than_three_questions() -> None:
    result = runner.invoke(app, ["review", "--questions", "4"])

    assert result.exit_code == 2
    assert "Invalid value" in result.output
    assert "1<=x<=3" in result.output


def test_review_stops_when_changed_files_have_no_analyzable_question_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "--quiet", "-b", "main"], cwd=tmp_path, check=True)
    binary = tmp_path / "image.bin"
    binary.write_bytes(b"\x00base")
    subprocess.run(["git", "add", "image.bin"], cwd=tmp_path, check=True)
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
            "Add binary",
        ],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", "-b", "feature/change-binary"],
        cwd=tmp_path,
        check=True,
    )
    binary.write_bytes(b"\x00changed")
    subprocess.run(["git", "add", "image.bin"], cwd=tmp_path, check=True)
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
            "Change binary",
        ],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["review", "--base", "main", "--plain"])

    assert result.exit_code == 0
    assert "Diff summary" in result.output
    assert "Review stopped: changed files contain no analyzable question evidence." in result.output
    assert "Question 1" not in result.output


def test_review_does_not_hide_unexpected_question_generation_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_python_feature_repository(tmp_path)
    monkeypatch.chdir(tmp_path)

    def raise_unexpected_error(
        generator: QuestionGenerator,
        context: ChangeContext,
    ) -> tuple[Question, ...]:
        raise ValueError("unexpected question-generation failure")

    monkeypatch.setattr(QuestionGenerator, "generate", raise_unexpected_error)

    result = runner.invoke(app, ["review", "--base", "main", "--plain"])

    assert result.exit_code == 1
    assert isinstance(result.exception, ValueError)
    assert str(result.exception) == "unexpected question-generation failure"
    assert (
        "Review stopped: changed files contain no analyzable question evidence."
        not in result.output
    )
