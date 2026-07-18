"""Detect likely side effects from bounded change evidence."""

from __future__ import annotations

from coderecall.core.types import ChangeContext


class SideEffectDetector:
    """Attach cautious side-effect inferences to a change context."""

    def detect(self, context: ChangeContext) -> ChangeContext:
        """Return the context unchanged when no side-effect signals exist."""

        return context
