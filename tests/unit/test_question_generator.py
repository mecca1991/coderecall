"""Tests for branch-specific question generation."""

from pathlib import Path

import pytest

from coderecall.analysis import QuestionGenerator
from coderecall.core.types import (
    ChangeContext,
    ChangedFile,
    ChangedSymbol,
    FileStatus,
    QuestionCategory,
)


def test_generates_three_branch_specific_questions_in_stable_order() -> None:
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/order-processing",
        base_branch="main",
        changed_files=(ChangedFile(path=Path("src/orders.py"), status=FileStatus.MODIFIED),),
        changed_symbols=(
            ChangedSymbol(
                file_path=Path("src/orders.py"),
                name="process_order",
                kind="function",
                line_start=12,
            ),
        ),
    )

    questions = QuestionGenerator().generate(context)

    assert tuple(question.id for question in questions) == ("behavior", "failure", "evidence")
    assert tuple(question.category for question in questions) == (
        QuestionCategory.BEHAVIOR,
        QuestionCategory.FAILURE,
        QuestionCategory.EVIDENCE,
    )
    assert all("`process_order`" in question.prompt for question in questions)
    assert all('"src/orders.py"' in question.prompt for question in questions)
    assert all(question.rationale for question in questions)
    assert all(question.references for question in questions)
    assert {citation.file_path for question in questions for citation in question.references} == {
        Path("src/orders.py")
    }
    assert questions == QuestionGenerator().generate(context)


def test_refuses_to_generate_generic_questions_without_meaningful_files() -> None:
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/generated-only",
        base_branch="main",
    )

    with pytest.raises(ValueError, match="meaningful changed file"):
        QuestionGenerator().generate(context)
