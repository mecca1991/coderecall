"""CLI smoke tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from coderecall.analysis import QuestionGenerator
from coderecall.cli.app import app
from coderecall.core.types import (
    ChangeContext,
    Question,
)

runner = CliRunner()

PRIVACY_DISCLOSURE = (
    "Privacy\n"
    "Model mode: Local heuristic (no remote model)\n"
    "Repository content, answers, and reports stay on this machine.\n"
    "CodeRecall sends no telemetry and makes no network requests.\n"
    "\n"
)
UNSUPPORTED_DART_NOTE = (
    "Symbol-level analysis was unavailable for Dart (.dart); any symbols inferred from hunk "
    "context are heuristic."
)


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


def create_dart_feature_repository(directory: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=directory, check=True)
    subprocess.run(["git", "checkout", "--quiet", "-b", "main"], cwd=directory, check=True)
    subprocess.run(["git", "config", "user.name", "CodeRecall Tests"], cwd=directory, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@coderecall.local"],
        cwd=directory,
        check=True,
    )
    source = directory / "lib" / "main.dart"
    source.parent.mkdir()
    source.write_text("String status() => 'pending';\n")
    subprocess.run(["git", "add", "lib/main.dart"], cwd=directory, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "Add Flutter status"],
        cwd=directory,
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", "-b", "feature/complete-status"],
        cwd=directory,
        check=True,
    )
    source.write_text("String status() => 'complete';\n")
    subprocess.run(["git", "add", "lib/main.dart"], cwd=directory, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "Complete Flutter status"],
        cwd=directory,
        check=True,
    )


def add_documentation_change(directory: Path) -> None:
    readme = directory / "README.md"
    readme.write_text("# Release plan\n\nCall `client.post()` before `status()`.\n")
    subprocess.run(["git", "add", "README.md"], cwd=directory, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "Document release plan"],
        cwd=directory,
        check=True,
    )


def create_documentation_only_feature_repository(directory: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=directory, check=True)
    subprocess.run(["git", "checkout", "--quiet", "-b", "main"], cwd=directory, check=True)
    subprocess.run(["git", "config", "user.name", "CodeRecall Tests"], cwd=directory, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@coderecall.local"],
        cwd=directory,
        check=True,
    )
    readme = directory / "README.md"
    readme.write_text("# Project\n")
    subprocess.run(["git", "add", "README.md"], cwd=directory, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "Add project documentation"],
        cwd=directory,
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", "-b", "feature/release-plan"],
        cwd=directory,
        check=True,
    )
    readme.write_text("# Project\n\n## Release plan\n\nShip after verification.\n")
    subprocess.run(["git", "add", "README.md"], cwd=directory, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "Add release plan"],
        cwd=directory,
        check=True,
    )


def test_root_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "review" in result.output
    assert "install-hook" in result.output
    assert "init" in result.output
    assert "Privacy" not in result.output


def test_review_help_lists_mvp_options() -> None:
    result = runner.invoke(app, ["review", "--help"], terminal_width=140)

    assert result.exit_code == 0
    assert "--base" in result.output
    assert "--report" in result.output
    assert "--questions" in result.output
    assert "--no-follow-up" in result.output
    assert "--include-uncomm" in result.output
    assert "--plain" in result.output
    assert "--model" not in result.output
    assert "Privacy" not in result.output


def test_version_option() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "coderecall 0.1.0" in result.output
    assert "Privacy" not in result.output


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
    assert result.output.startswith(PRIVACY_DISCLOSURE)
    assert result.output.index("Privacy") < result.output.index("CodeRecall review")
    assert "CodeRecall review" in result.output
    assert f'Repository: "{tmp_path}"' in result.output
    assert "Branch: feature/cli-context -> main" in result.output
    assert "Changes: 1 total, 1 analyzed, 0 filtered" in result.output
    assert '  - modified: "tracked.txt"' in result.output
    assert "Change summary" in result.output
    assert (
        "Purpose: Likely updates 1 meaningful file across .txt; a symbol-level purpose could "
        "not be inferred."
    ) in result.output
    assert result.output.index("Change summary") < result.output.index("Question 1/3 — Behavior")
    assert "Skipped." in result.output
    assert "End of input: 2 remaining questions skipped." in result.output
    assert "Session complete" in result.output
    assert "Answers: 0 answered, 3 skipped" in result.output
    report_path = tmp_path / "coderecall-report.md"
    assert report_path.is_file()
    assert f'Report written: "{tmp_path / "coderecall-report.md"}"' in result.output
    report = report_path.read_text(encoding="utf-8")
    assert "## Review Talking Points\n\n- Explain the change:" in report
    assert "No review talking points generated." not in report


def test_review_writes_custom_report_relative_to_invocation_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_python_feature_repository(tmp_path)
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "artifacts" / "understanding.md"

    result = runner.invoke(
        app,
        [
            "review",
            "--base",
            "main",
            "--questions",
            "1",
            "--no-follow-up",
            "--report",
            "artifacts/understanding.md",
            "--plain",
        ],
        input="It changes process_order to return complete.\n\n",
    )

    assert result.exit_code == 0
    assert target.is_file()
    assert not (tmp_path / "coderecall-report.md").exists()
    assert f'Report written: "{target}"' in result.output
    assert result.output.index("Answers: 1 answered, 0 skipped") < result.output.index(
        "Report written:"
    )
    report = target.read_text(encoding="utf-8")
    assert "# CodeRecall Report" in report
    assert "Branch: feature/complete-order" in report
    assert "Base branch: main" in report
    assert "Model mode: Local heuristic (no remote model)" in report
    assert "> It changes process_order to return complete." in report
    assert "## Review Talking Points\n\n- Explain the change:" in report
    assert "No review talking points generated." not in report
    assert "Explain the change:" not in result.output


def test_review_discloses_dart_analysis_limit_in_terminal_and_default_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_dart_feature_repository(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["review", "--base", "main", "--questions", "1", "--no-follow-up", "--plain"],
        input="\n",
    )

    assert result.exit_code == 0
    assert "Likely updates code in 1 meaningful file." not in result.output
    assert (
        "Purpose: Likely updates 1 meaningful file across Dart (.dart); a symbol-level purpose "
        "could not be inferred."
    ) in result.output
    assert UNSUPPORTED_DART_NOTE in result.output

    report = (tmp_path / "coderecall-report.md").read_text(encoding="utf-8")
    assert f"**Uncertainty**\n\n- {UNSUPPORTED_DART_NOTE}" in report


def test_review_uses_dart_subjects_when_documentation_is_listed_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_dart_feature_repository(tmp_path)
    add_documentation_change(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["review", "--base", "main", "--questions", "3", "--no-follow-up", "--plain"],
        input="\n\n\n",
    )

    assert result.exit_code == 0
    assert result.output.index('added: "README.md"') < result.output.index(
        'modified: "lib/main.dart"'
    )
    question_output = result.output.split("Question 1/3", maxsplit=1)[1]
    assert question_output.count('"lib/main.dart"') >= 3
    assert '"README.md"' not in question_output


def test_review_stops_for_documentation_only_changes_without_writing_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_documentation_only_feature_repository(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["review", "--base", "main", "--plain"])

    assert result.exit_code == 0
    assert 'modified: "README.md"' in result.output
    assert "Change summary" in result.output
    assert (
        "Review stopped\nChanged files contain only documentation or planning changes."
        in result.output
    )
    assert "Question 1" not in result.output
    assert not (tmp_path / "coderecall-report.md").exists()


def test_review_preserves_session_output_when_report_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_python_feature_repository(tmp_path)
    monkeypatch.chdir(tmp_path)
    blocked_parent = tmp_path / "blocked"
    blocked_parent.write_text("not a directory", encoding="utf-8")
    target = blocked_parent / "report.md"

    result = runner.invoke(
        app,
        [
            "review",
            "--base",
            "main",
            "--questions",
            "1",
            "--no-follow-up",
            "--report",
            str(target),
            "--plain",
        ],
        input="Answer.\n\n",
    )

    assert result.exit_code == 1
    assert "CodeRecall review" in result.output
    assert "Change summary" in result.output
    assert "Question 1/1" in result.output
    assert "Session complete\nAnswers: 1 answered, 0 skipped" in result.output
    assert f'Could not write the local report to "{target}"' in result.output
    assert "--report <path>" in result.output
    assert "Report written:" not in result.output
    assert isinstance(result.exception, SystemExit)


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
    assert "Changes: 2 total, 1 analyzed, 1 filtered" in result.output
    assert 'modified: "app.py"' in result.output
    assert "Filtered files:" in result.output
    assert 'modified: "package-lock.json" (filtered: lockfile)' in result.output


def test_review_fails_clearly_outside_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["review"])

    assert result.exit_code == 1
    assert result.output.startswith(PRIVACY_DISCLOSURE)
    assert result.output.index("Privacy") < result.output.index("could not find a Git repository")
    assert "could not find a Git repository" in result.output
    assert "Run this command inside a Git working tree." in result.output
    assert "git rev-parse --show-toplevel" in result.output
    assert not (tmp_path / "coderecall-report.md").exists()


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
    assert "Changes: 1 total, 0 analyzed, 1 filtered" in result.output
    assert 'modified: "package-lock.json" (filtered: lockfile)' in result.output
    assert "Purpose: No meaningful code changes were available to summarize." in result.output
    assert "Review stopped\nNo meaningful files remain after filtering." in result.output
    assert "Questions" not in result.output
    assert "A blank line submits" not in result.output
    assert not (tmp_path / "coderecall-report.md").exists()


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
            if f"— {category.title()}" in result.output
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
    assert "Change summary" in result.output
    assert "Review stopped\nChanged files contain no analyzable question evidence." in result.output
    assert "Question 1" not in result.output
    assert not (tmp_path / "coderecall-report.md").exists()


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
    assert "Changed files contain no analyzable question evidence." not in result.output
