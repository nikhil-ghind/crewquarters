"""Token chunking (PLAN.md section 9.1, step 6): about 800 tokens with about 120 tokens of
overlap, never crossing a document boundary, preferring to end at a segment boundary.

Tokens are approximated as words and punctuation marks (``\\w+|[^\\w\\s]``), which is
close to what the pinned BPE embedding model sees and needs no tokenizer download.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from typing import Any

from crewquarters_knowledge.extract import Segment

TOKEN = re.compile(r"\w+|[^\w\s]")
SEPARATOR = "\n\n"
# End a chunk at a segment boundary if one falls in the last quarter of the window.
SNAP_FRACTION = 0.75


def count_tokens(text: str) -> int:
    return sum(1 for _ in TOKEN.finditer(text))


@dataclass(frozen=True)
class Chunk:
    text: str
    token_count: int
    locator: dict[str, Any]


def chunk(segments: list[Segment], max_tokens: int, overlap: int) -> list[Chunk]:
    if not 0 <= overlap < max_tokens:
        raise ValueError("overlap must be smaller than the chunk size")
    text = SEPARATOR.join(s.text for s in segments)
    starts: list[int] = []
    offset = 0
    for s in segments:
        starts.append(offset)
        offset += len(s.text) + len(SEPARATOR)
    spans = [m.span() for m in TOKEN.finditer(text)]
    # Token indexes where a new segment begins: preferred chunk ends.
    boundaries = sorted({bisect.bisect_left(spans, (start, 0)) for start in starts[1:]})

    chunks: list[Chunk] = []
    first = 0
    while first < len(spans):
        end = min(first + max_tokens, len(spans))
        if end < len(spans):
            floor = first + int(max_tokens * SNAP_FRACTION)
            candidates = [b for b in boundaries if floor < b <= end]
            if candidates:
                end = candidates[-1]
        begin_char, end_char = spans[first][0], spans[end - 1][1]
        chunks.append(
            Chunk(
                text=text[begin_char:end_char],
                token_count=end - first,
                locator=_locator(segments, starts, begin_char, end_char - 1),
            )
        )
        if end == len(spans):
            break
        first = max(end - overlap, first + 1)
    return chunks


def _locator(segments: list[Segment], starts: list[int], begin: int, last: int) -> dict[str, Any]:
    head = segments[bisect.bisect_right(starts, begin) - 1].locator
    tail = segments[bisect.bisect_right(starts, last) - 1].locator
    return dict(head) if tail == head else {**head, "through": dict(tail)}


def _position(loc: dict[str, Any]) -> str:
    for key in ("page", "row", "paragraph", "line"):
        if key in loc:
            prefix = f"table {loc['table']}, " if "table" in loc else ""
            return f"{prefix}{key} {loc[key]}"
    return ""


def _labelled(section: Any, position: str) -> str:
    if section and position:
        return f"{section} ({position})"
    return str(section or position)


def describe(locator: dict[str, Any]) -> str:
    """A human-readable location for citations, e.g. ``page 3-4`` or ``Refunds (line 2)``."""
    through = locator.get("through")
    start = _position(locator)
    if not isinstance(through, dict):
        return _labelled(locator.get("section"), start)
    if through.get("section") != locator.get("section"):
        return f"{_labelled(locator.get('section'), start)} to " + _labelled(
            through.get("section"), _position(through)
        )
    end = _position(through)
    if end and end.rsplit(" ", 1)[0] == start.rsplit(" ", 1)[0]:
        span = f"{start}-{end.rsplit(' ', 1)[1]}"
    else:
        span = f"{start} to {end}" if end else start
    return _labelled(locator.get("section"), span)
