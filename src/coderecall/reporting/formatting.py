"""Shared safe formatting for local Markdown report content."""

from __future__ import annotations

import re


def inline_code(value: str) -> str:
    """Wrap arbitrary text in a Markdown code span without changing its content."""

    longest_run = max((len(run) for run in re.findall(r"`+", value)), default=0)
    delimiter = "`" * max(1, longest_run + 1)
    padding = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{delimiter}{padding}{value}{padding}{delimiter}"
