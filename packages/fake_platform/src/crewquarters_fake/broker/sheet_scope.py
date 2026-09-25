"""Sheets scoping, as the real broker does it: calls use only the configured ``spreadsheetId``,
reads stay within the configured ``inputRange`` and writes within ``resultRange``.

``Area`` and ``parse`` are a copy of ``crewquarters_broker.a1`` (the fake platform does not
depend on the broker package), and ``configured``/``within`` mirror ``Grant.configured`` and
``_within`` in ``crewquarters_broker.auth``/``agent_api``, error codes, statuses and details
included. Keep them identical; ``tests/test_sheets_scope_parity.py`` checks that they are.
Missing bounds are open: ``A:H`` is every row of columns A-H, ``A2:D`` is columns A-D from
row 2 down, and ``Contacts`` alone is the whole tab."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from crewquarters_fake.errors import ApiError

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


def configured(config: dict[str, Any], key: str, requested: str | None = None) -> str:
    """A resource ID the owner chose in the installation config. When the agent names one
    too, it must be that same resource."""
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ApiError(
            409, "NEEDS_CONFIGURATION", f"The installation config has no {key}.", {"key": key}
        )
    if requested is not None and requested != value:
        raise ApiError(
            403, "PERMISSION_DENIED", f"Only the configured {key} may be used.", {"key": key}
        )
    return value


def within(config: dict[str, Any], key: str, requested: str) -> None:
    """The requested A1 range lies inside the range the owner configured under ``key``:
    reads within ``inputRange``, writes within ``resultRange`` (same tab)."""
    try:
        allowed = parse(configured(config, key))
    except ValueError:
        raise ApiError(
            409,
            "NEEDS_CONFIGURATION",
            f"The installation config's {key} is not valid.",
            {"key": key},
        ) from None
    try:
        area = parse(requested)
    except ValueError:
        raise ApiError(
            422, "INVALID_REQUEST", "The range must be A1 notation with a tab."
        ) from None
    if not allowed.contains(area):
        raise ApiError(
            403,
            "PERMISSION_DENIED",
            f"Only cells within the configured {key} may be used.",
            {"key": key},
        )
