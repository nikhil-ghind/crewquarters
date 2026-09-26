"""Unified-diff handling: number the new-side lines for the model, and know which lines a
GitHub review comment may target (an added or unchanged line inside a hunk)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class AnnotatedPatch:
    text: str
    # New-file line numbers that appear in ``text`` and can carry a review comment.
    lines: frozenset[int]
    truncated: bool


def annotate(patch: str, max_chars: int) -> AnnotatedPatch:
    """Render ``patch`` with the new-file line number in front of every added or unchanged line,
    stopping at a line boundary once ``max_chars`` is reached."""
    rendered: list[str] = []
    lines: set[int] = set()
    size = 0
    current = 0
    truncated = False
    for raw in patch.splitlines():
        header = _HUNK.match(raw)
        if header:
            current = int(header.group(1))
            out = raw
            number = None
        elif not current or raw.startswith("\\"):
            continue  # before the first hunk, or "\ No newline at end of file"
        elif raw.startswith("-"):
            out, number = f"      {raw}", None
        else:
            marker = "+" if raw.startswith("+") else " "
            number = current
            out = f"{current:>5} {marker}{raw[1:]}"
            current += 1
        if size + len(out) + 1 > max_chars:
            truncated = True
            break
        rendered.append(out)
        size += len(out) + 1
        if number is not None:
            lines.add(number)
    return AnnotatedPatch("\n".join(rendered), frozenset(lines), truncated)
