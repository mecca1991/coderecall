"""Tests for deterministic local Markdown reports."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from coderecall.core.errors import ReportWriteFailed
from coderecall.core.types import (
    Answer,
    Assessment,
    AssessmentLabel,
    ChangeContext,
    DiffSummary,
    EvidenceCitation,
    FollowUp,
    ModelMode,
    Question,
    QuestionCategory,
    Report,
)
from coderecall.reporting import MarkdownReportWriter, ReportBuilder

NOW = datetime(2026, 7, 18, 12, 30, 45, tzinfo=UTC)


def make_question(
    question_id: str = "behavior",
    *,
    prompt: str = "What changed?",
    references: tuple[EvidenceCitation, ...] = (),
) -> Question:
    return Question(
        id=question_id,
        category=QuestionCategory.BEHAVIOR,
        prompt=prompt,
        rationale="The branch changes behavior.",
        references=references,
    )


def make_assessment(
    question_id: str = "behavior",
    *,
    evidence: tuple[EvidenceCitation, ...] = (),
) -> Assessment:
    return Assessment(
        question_id=question_id,
        label=AssessmentLabel.PARTIAL,
        confidence="medium",
        summary="The response is directionally correct.",
        strengths=("Names the changed behavior.",),
        gaps=("Explain the failure path.",),
        uncertainty_notes=("A downstream implementation was not changed.",),
        evidence=evidence,
    )


def make_context() -> ChangeContext:
    return ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/reporting",
        base_branch="main",
    )


def test_builder_generates_utc_metadata_and_orders_payload_by_question() -> None:
    questions = (make_question("behavior"), make_question("failure"))
    answers = (
        Answer(question_id="failure", raw_text="Failure answer."),
        Answer(question_id="behavior", raw_text="Behavior answer."),
    )
    assessments = (make_assessment("failure"), make_assessment("behavior"))

    report = ReportBuilder(clock=lambda: NOW).build(
        make_context(),
        DiffSummary(purpose="Likely adds local reporting."),
        questions,
        answers,
        assessments,
    )

    assert report.session_metadata == {
        "branch": "feature/reporting",
        "base_branch": "main",
        "model_mode": "Local heuristic (no remote model)",
        "generated_at": "2026-07-18T12:30:45+00:00",
    }
    assert report.diff_summary == "Likely adds local reporting."
    assert tuple(answer.question_id for answer in report.answers) == ("behavior", "failure")
    assert tuple(item.question_id for item in report.assessments) == ("behavior", "failure")


def test_builder_propagates_summary_uncertainty_to_report() -> None:
    question = make_question()
    summary_note = (
        "Symbol-level analysis was unavailable for Dart (.dart); any symbols inferred from "
        "hunk context are heuristic."
    )

    report = ReportBuilder(clock=lambda: NOW).build(
        make_context(),
        DiffSummary(purpose="Summary.", uncertainty_notes=(summary_note,)),
        (question,),
        (Answer(question_id="behavior", raw_text="Answer."),),
        (make_assessment(),),
    )

    assert report.summary_uncertainty_notes == (summary_note,)
    assert f"**Uncertainty**\n\n- {summary_note}" in MarkdownReportWriter().render(report)


def test_render_without_summary_uncertainty_is_byte_for_byte_unchanged() -> None:
    report = Report(
        session_metadata={},
        diff_summary="Summary.",
        questions=(),
        answers=(),
        assessments=(),
    )

    assert MarkdownReportWriter().render(report) == (
        "# CodeRecall Report\n"
        "\n"
        "Branch: \n"
        "Base branch: \n"
        "Model mode: \n"
        "Generated: \n"
        "\n"
        "## Change Summary\n"
        "\n"
        "Summary.\n"
        "\n"
        "## Questions and Answers\n"
        "\n"
        "## Review Talking Points\n"
        "\n"
        "- No review talking points generated.\n"
    )


@pytest.mark.parametrize(
    ("questions", "answers", "assessments", "message"),
    (
        (
            (make_question(), make_question()),
            (Answer(question_id="behavior", raw_text="a"),) * 2,
            (make_assessment(),) * 2,
            "question IDs must be unique",
        ),
        (
            (make_question(),),
            (
                Answer(question_id="behavior", raw_text="a"),
                Answer(question_id="behavior", raw_text="b"),
            ),
            (make_assessment(),),
            "answer question IDs must be unique",
        ),
        (
            (make_question(),),
            (Answer(question_id="behavior", raw_text="a"),),
            (make_assessment(), make_assessment()),
            "assessment question IDs must be unique",
        ),
        (
            (make_question(),),
            (Answer(question_id="other", raw_text="a"),),
            (make_assessment(),),
            "question, answer, and assessment IDs must match",
        ),
    ),
)
def test_builder_rejects_invalid_initial_question_id_sets(
    questions: tuple[Question, ...],
    answers: tuple[Answer, ...],
    assessments: tuple[Assessment, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ReportBuilder(clock=lambda: NOW).build(
            make_context(),
            DiffSummary(purpose="Summary."),
            questions,
            answers,
            assessments,
        )


def test_render_includes_multiline_skipped_uncertain_and_evidence_rich_content() -> None:
    citation = EvidenceCitation(
        kind="call",
        file_path=Path("src/payments.py"),
        symbol="processor.charge",
        line_start=14,
        line_end=16,
        hunk_header="@@ -10,2 +14,4 @@ capture",
        note="External charge occurs before persistence.",
    )
    answered = make_question(
        prompt="Explain the flow.\n## This must remain quoted",
        references=(citation,),
    )
    skipped = Question(
        id="failure",
        category=QuestionCategory.FAILURE,
        prompt="What can fail?",
        rationale="A boundary changed.",
    )
    uncertain = Assessment(
        question_id="failure",
        label=AssessmentLabel.UNCERTAIN,
        confidence="low",
        summary="There is not enough evidence.",
        uncertainty_notes=("The implementation is outside the diff.",),
    )
    report = ReportBuilder(clock=lambda: NOW).build(
        make_context(),
        DiffSummary(purpose="Likely changes the payment flow."),
        (answered, skipped),
        (
            Answer(
                question_id="behavior",
                raw_text="It charges first.\n# This must remain quoted",
            ),
            Answer(question_id="failure", raw_text="", skipped=True),
        ),
        (make_assessment(evidence=(citation,)), uncertain),
    )

    rendered = MarkdownReportWriter().render(report)

    assert rendered.startswith(
        "# CodeRecall Report\n\n"
        "Branch: feature/reporting\n"
        "Base branch: main\n"
        "Model mode: Local heuristic (no remote model)\n"
        "Generated: 2026-07-18T12:30:45+00:00\n"
    )
    assert "## Change Summary\n\nLikely changes the payment flow." in rendered
    assert "### 1. Behavior" in rendered
    assert "> Explain the flow.\n> ## This must remain quoted" in rendered
    assert "> It charges first.\n> # This must remain quoted" in rendered
    assert "### 2. Failure" in rendered
    assert "**Answer**\n\n> **Skipped.**" in rendered
    assert "**Assessment:** Uncertain" in rendered
    assert "**Confidence:** low" in rendered
    assert "- The implementation is outside the diff." in rendered
    assert (
        "- `src/payments.py`; symbol `processor.charge`; lines 14-16; "
        "hunk `@@ -10,2 +14,4 @@ capture`; note: External charge occurs before persistence."
        in rendered
    )
    assert "## Follow-Up" not in rendered
    assert rendered.endswith("## Review Talking Points\n\n- No review talking points generated.\n")


def test_builder_accepts_model_mode_as_a_keyword_only_argument() -> None:
    question = make_question()

    report = ReportBuilder(clock=lambda: NOW).build(
        make_context(),
        DiffSummary(purpose="Summary."),
        (question,),
        (Answer(question_id="behavior", raw_text="Answer."),),
        (make_assessment(),),
        model_mode=ModelMode.LOCAL_HEURISTIC,
    )

    assert report.session_metadata["model_mode"] == "Local heuristic (no remote model)"


def test_render_includes_follow_up_answer_citations_and_optional_assessment() -> None:
    citation = EvidenceCitation(
        kind="call",
        file_path=Path("src/payments.py"),
        symbol="database.transaction",
        line_start=20,
    )
    initial = make_question(references=(citation,))
    follow_up_question = Question(
        id="failure-follow-up",
        category=QuestionCategory.FOLLOW_UP,
        prompt="How should recovery work?",
        rationale="A rollback gap remains.",
        references=(citation,),
    )
    follow_up = FollowUp(
        question=follow_up_question,
        answer=Answer(
            question_id="failure-follow-up",
            raw_text="Reconcile pending charges.",
        ),
        assessment=Assessment(
            question_id="failure-follow-up",
            label=AssessmentLabel.STRONG,
            confidence="medium",
            summary="The response addresses recovery.",
            evidence=(citation,),
        ),
    )
    report = ReportBuilder(clock=lambda: NOW).build(
        make_context(),
        DiffSummary(purpose="Summary."),
        (initial,),
        (Answer(question_id="behavior", raw_text="Answer."),),
        (make_assessment(evidence=(citation,)),),
        follow_up=follow_up,
        review_talking_points=("Discuss recovery.",),
    )

    rendered = MarkdownReportWriter().render(report)

    assert "## Follow-Up" in rendered
    assert "> How should recovery work?" in rendered
    assert "> Reconcile pending charges." in rendered
    assert "- `src/payments.py`; symbol `database.transaction`; line 20" in rendered
    assert "**Assessment:** Strong" in rendered
    assert rendered.endswith("## Review Talking Points\n\n- Discuss recovery.\n")


def test_render_pads_inline_code_that_starts_or_ends_with_a_backtick() -> None:
    citation = EvidenceCitation(
        kind="call",
        file_path=Path("`src/report.py"),
        symbol="render`",
        hunk_header="`",
    )
    question = make_question(references=(citation,))
    report = ReportBuilder(clock=lambda: NOW).build(
        make_context(),
        DiffSummary(purpose="Summary."),
        (question,),
        (Answer(question_id="behavior", raw_text="Answer."),),
        (make_assessment(evidence=(citation,)),),
    )

    rendered = MarkdownReportWriter().render(report)

    assert "- `` `src/report.py ``; symbol `` render` ``; hunk `` ` ``" in rendered


def test_render_allows_follow_up_without_assessment() -> None:
    question = make_question()
    follow_up_question = make_question("behavior-follow-up", prompt="One more detail?")
    report = ReportBuilder(clock=lambda: NOW).build(
        make_context(),
        DiffSummary(purpose="Summary."),
        (question,),
        (Answer(question_id="behavior", raw_text="Answer."),),
        (make_assessment(),),
        follow_up=FollowUp(
            question=follow_up_question,
            answer=Answer(question_id="behavior-follow-up", raw_text="Detail."),
        ),
    )

    follow_up_section = MarkdownReportWriter().render(report).split("## Follow-Up", 1)[1]

    assert "> Detail." in follow_up_section
    assert "**Assessment:**" not in follow_up_section


def test_write_creates_nested_utf8_file_and_overwrites_existing_content(tmp_path: Path) -> None:
    question = make_question(prompt="What does café handling change?")
    report = ReportBuilder(clock=lambda: NOW).build(
        make_context(),
        DiffSummary(purpose="Supports café names."),
        (question,),
        (Answer(question_id="behavior", raw_text="It preserves café."),),
        (make_assessment(),),
    )
    target = tmp_path / "nested" / "report.md"
    target.parent.mkdir()
    target.write_text("old content", encoding="utf-8")

    written = MarkdownReportWriter().write(report, target)

    assert written == target
    assert target.read_text(encoding="utf-8") == MarkdownReportWriter().render(report)
    assert "café" in target.read_text(encoding="utf-8")


def test_write_wraps_filesystem_errors_with_target_and_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = make_question()
    report = ReportBuilder(clock=lambda: NOW).build(
        make_context(),
        DiffSummary(purpose="Summary."),
        (question,),
        (Answer(question_id="behavior", raw_text="Answer."),),
        (make_assessment(),),
    )
    target = tmp_path / "report.md"
    failure = PermissionError("read-only location")

    def fail_write(path: Path, *args: object, **kwargs: object) -> int:
        raise failure

    monkeypatch.setattr(Path, "write_text", fail_write)

    with pytest.raises(ReportWriteFailed) as captured:
        MarkdownReportWriter().write(report, target)

    assert captured.value.target_path == target
    assert captured.value.underlying_error is failure
    assert str(target) in captured.value.message
    assert "read-only location" in captured.value.message
    assert captured.value.recovery is not None
    assert "--report" in captured.value.recovery
