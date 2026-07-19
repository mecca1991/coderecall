"""Tests for repository-local CodeRecall configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from coderecall.config import (
    ConfigLoader,
    EffectiveReviewOptions,
    ProjectConfig,
    anchor_path,
    resolve_review_options,
)
from coderecall.core.errors import CodeRecallError


def write_config(repository_root: Path, content: str) -> Path:
    config_path = repository_root / ".coderecall.yml"
    config_path.write_text(content, encoding="utf-8")
    return config_path


def test_missing_config_preserves_empty_project_config(tmp_path: Path) -> None:
    assert ConfigLoader().load(tmp_path) == ProjectConfig()


@pytest.mark.parametrize("content", ("", "\n", "---\n", "null\n"))
def test_empty_config_preserves_empty_project_config(tmp_path: Path, content: str) -> None:
    write_config(tmp_path, content)

    assert ConfigLoader().load(tmp_path) == ProjectConfig()


def test_loads_complete_config(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """base: main
report_path: coderecall-report.md
questions: 3
include_uncommitted: false
exclude:
  - node_modules/**
  - dist/**
  - build/**
  - vendor/**
""",
    )

    assert ConfigLoader().load(tmp_path) == ProjectConfig(
        base="main",
        report_path="coderecall-report.md",
        questions=3,
        include_uncommitted=False,
        exclude=("node_modules/**", "dist/**", "build/**", "vendor/**"),
    )


def test_loads_partial_config(tmp_path: Path) -> None:
    write_config(tmp_path, "questions: 1\n")

    assert ConfigLoader().load(tmp_path) == ProjectConfig(questions=1)


@pytest.mark.parametrize("content", ("- base: main\n", "base\n"))
def test_rejects_non_mapping_document(tmp_path: Path, content: str) -> None:
    config_path = write_config(tmp_path, content)

    with pytest.raises(CodeRecallError) as captured:
        ConfigLoader().load(tmp_path)

    assert str(config_path) in captured.value.message
    assert "top-level mapping" in captured.value.message
    assert captured.value.recovery is not None


def test_rejects_unknown_keys_in_stable_order(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, "zebra: true\nalpha: false\n")

    with pytest.raises(CodeRecallError) as captured:
        ConfigLoader().load(tmp_path)

    assert str(config_path) in captured.value.message
    assert "alpha, zebra" in captured.value.message
    assert "base, exclude, include_uncommitted, questions, report_path" in (
        captured.value.recovery or ""
    )


@pytest.mark.parametrize(
    ("content", "expected"),
    (
        ("base: [main]\n", "base"),
        ("base: '   '\n", "base"),
        ("report_path: 42\n", "report_path"),
        ("report_path: ''\n", "report_path"),
        ("questions: true\n", "questions"),
        ("questions: '2'\n", "questions"),
        ("questions: 0\n", "questions"),
        ("questions: 4\n", "questions"),
        ("include_uncommitted: 1\n", "include_uncommitted"),
        ("include_uncommitted: 'false'\n", "include_uncommitted"),
        ("exclude: dist/**\n", "exclude"),
        ("exclude: [dist/**, 3]\n", "exclude"),
        ("exclude: [dist/**, '']\n", "exclude"),
    ),
)
def test_rejects_invalid_field_values(tmp_path: Path, content: str, expected: str) -> None:
    config_path = write_config(tmp_path, content)

    with pytest.raises(CodeRecallError) as captured:
        ConfigLoader().load(tmp_path)

    assert str(config_path) in captured.value.message
    assert expected in captured.value.message
    assert captured.value.recovery is not None


@pytest.mark.parametrize(
    ("pattern", "expected"),
    (
        ("!src/keep.py", "negated"),
        ("../secrets/**", "traversal"),
        ("src/../../secrets/**", "traversal"),
    ),
)
def test_rejects_non_positive_or_traversing_exclusions(
    tmp_path: Path,
    pattern: str,
    expected: str,
) -> None:
    config_path = write_config(tmp_path, f"exclude:\n  - {pattern!r}\n")

    with pytest.raises(CodeRecallError) as captured:
        ConfigLoader().load(tmp_path)

    assert str(config_path) in captured.value.message
    assert expected in captured.value.message.lower()


@pytest.mark.parametrize(
    "content",
    (
        "base: [unterminated\n",
        "base: !unsafe main\n",
        "!!python/object/apply:os.system ['echo unsafe']\n",
    ),
)
def test_rejects_malformed_or_unsafe_yaml(tmp_path: Path, content: str) -> None:
    config_path = write_config(tmp_path, content)

    with pytest.raises(CodeRecallError) as captured:
        ConfigLoader().load(tmp_path)

    assert str(config_path) in captured.value.message
    assert "valid YAML" in captured.value.message
    assert "Fix the YAML syntax" in (captured.value.recovery or "")


def test_rejects_unreadable_config_path(tmp_path: Path) -> None:
    config_path = tmp_path / ".coderecall.yml"
    config_path.mkdir()

    with pytest.raises(CodeRecallError) as captured:
        ConfigLoader().load(tmp_path)

    assert str(config_path) in captured.value.message
    assert "read" in captured.value.message.lower()
    assert "readable" in (captured.value.recovery or "").lower()


def test_rejects_dangling_config_symlink_as_unreadable(tmp_path: Path) -> None:
    config_path = tmp_path / ".coderecall.yml"
    config_path.symlink_to(tmp_path / "missing-config.yml")

    with pytest.raises(CodeRecallError) as captured:
        ConfigLoader().load(tmp_path)

    assert str(config_path) in captured.value.message
    assert "read" in captured.value.message.lower()
    assert "readable" in (captured.value.recovery or "").lower()


def test_resolves_defaults_relative_to_invocation_directory(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    invocation_directory = repository_root / "src"

    options = resolve_review_options(
        config=ProjectConfig(),
        repository_root=repository_root,
        invocation_directory=invocation_directory,
    )

    assert options == EffectiveReviewOptions(
        base=None,
        report_path=invocation_directory / "coderecall-report.md",
        questions=3,
        include_uncommitted=False,
        exclude=(),
    )


def test_resolves_configured_values_and_anchors_report_to_repository(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    invocation_directory = repository_root / "src"
    config = ProjectConfig(
        base="develop",
        report_path="artifacts/review.md",
        questions=2,
        include_uncommitted=True,
        exclude=("generated/**",),
    )

    options = resolve_review_options(
        config=config,
        repository_root=repository_root,
        invocation_directory=invocation_directory,
    )

    assert options == EffectiveReviewOptions(
        base="develop",
        report_path=repository_root / "artifacts/review.md",
        questions=2,
        include_uncommitted=True,
        exclude=("generated/**",),
    )


def test_explicit_cli_values_override_every_overlapping_config_value(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    invocation_directory = repository_root / "src"
    config = ProjectConfig(
        base="develop",
        report_path="configured.md",
        questions=3,
        include_uncommitted=True,
        exclude=("configured/**",),
    )

    options = resolve_review_options(
        config=config,
        repository_root=repository_root,
        invocation_directory=invocation_directory,
        base="main",
        report_path=Path("cli.md"),
        questions=1,
        include_uncommitted=False,
    )

    assert options == EffectiveReviewOptions(
        base="main",
        report_path=invocation_directory / "cli.md",
        questions=1,
        include_uncommitted=False,
        exclude=("configured/**",),
    )


def test_absolute_report_paths_are_preserved(tmp_path: Path) -> None:
    absolute_config_path = tmp_path / "configured.md"
    absolute_cli_path = tmp_path / "cli.md"

    configured = resolve_review_options(
        config=ProjectConfig(report_path=str(absolute_config_path)),
        repository_root=tmp_path / "repository",
        invocation_directory=tmp_path / "invocation",
    )
    cli = resolve_review_options(
        config=ProjectConfig(report_path="configured.md"),
        repository_root=tmp_path / "repository",
        invocation_directory=tmp_path / "invocation",
        report_path=absolute_cli_path,
    )

    assert configured.report_path == absolute_config_path
    assert cli.report_path == absolute_cli_path


def test_anchor_path_anchors_relative_path_to_directory(tmp_path: Path) -> None:
    assert anchor_path(Path("reports/review.md"), tmp_path) == (tmp_path / "reports" / "review.md")


def test_anchor_path_preserves_absolute_path(tmp_path: Path) -> None:
    absolute_path = tmp_path / "reports" / "review.md"

    assert anchor_path(absolute_path, tmp_path / "other") == absolute_path
