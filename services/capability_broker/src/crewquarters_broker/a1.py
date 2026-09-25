"""A1 ranges (``Contacts!A2:D``) and containment, for scoping Sheets calls to the ranges the
owner configured. Missing bounds are open: ``A:H`` is every row of columns A-H, ``A2:D`` is
columns A-D from row 2 down, and ``Contacts`` alone is the whole tab."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

_REF = re.compile(r"^([A-Za-z]{0,3})([1-9][0-9]{0,6})?$")


@dataclass(frozen=True)
class Area:
    tab: str
    first_col: float
    first_row: float
    last_col: float
    last_row: float

    def contains(self, other: Area) -> bool:
        return (
            self.tab.casefold() == other.tab.casefold()  # A1 tab names ignore case
            and self.first_col <= other.first_col
            and other.last_col <= self.last_col
            and self.first_row <= other.first_row
            and other.last_row <= self.last_row
        )


def _column(letters: str) -> int:
    index = 0
    for char in letters.upper():
        index = index * 26 + ord(char) - ord("A") + 1
    return index


def _split_tab(text: str) -> tuple[str, str | None]:
    if text.startswith("'"):
        match = re.fullmatch(r"'((?:[^']|'')+)'(?:!(.*))?", text, re.DOTALL)
        if match is None:
            raise ValueError("malformed quoted tab name")
        return match.group(1).replace("''", "'"), match.group(2)
    tab, bang, ref = text.partition("!")
    if not bang:  # a bare tab name; a bare cell range would mean "the first tab"
        if all(p and _REF.match(p) for p in text.split(":")):
            raise ValueError("a range must name its tab")
        return text, None
    return tab, ref


def parse(text: str) -> Area:
    """Parse ``Tab!A1:B2`` (the tab is required). Raises ``ValueError``."""
    tab, ref = _split_tab(text.strip())
    if not tab or "!" in tab or (ref is not None and "!" in ref):
        raise ValueError("malformed range")
    if ref is None:
        return Area(tab, 1, 1, math.inf, math.inf)
    parts = ref.split(":")
    matches = [_REF.match(p) for p in parts]
    if len(parts) > 2 or any(m is None or not p for m, p in zip(matches, parts, strict=True)):
        raise ValueError("malformed range")
    (c1, r1), *rest = [(m.group(1), m.group(2)) for m in matches if m is not None]
    first_col = _column(c1) if c1 else 1
    first_row = int(r1) if r1 else 1
    if rest:
        c2, r2 = rest[0]
    else:  # one reference: that cell, column, or row
        c2, r2 = c1, r1
    last_col = _column(c2) if c2 else math.inf
    last_row = int(r2) if r2 else math.inf
    if last_col < first_col or last_row < first_row:
        raise ValueError("malformed range")
    return Area(tab, first_col, first_row, last_col, last_row)
