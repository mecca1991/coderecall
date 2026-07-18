"""Tests for detecting likely side effects from change evidence."""

from __future__ import annotations

from pathlib import Path

from coderecall.analysis.side_effect_detector import SideEffectDetector
from coderecall.core.types import ChangeContext


def test_preserves_context_when_no_side_effect_signals_exist() -> None:
    context = ChangeContext(
        repo_root=Path("/repo"),
        current_branch="feature/read-only-change",
        base_branch="main",
    )

    detected = SideEffectDetector().detect(context)

    assert detected is context
    assert detected.likely_side_effects == ()
