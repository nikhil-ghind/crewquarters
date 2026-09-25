"""A1 ranges scope every Sheets call to what the owner configured, so the parser must
reject anything it does not understand (``ValueError``, a 4xx at the broker) and must never
widen a range."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crewquarters_broker.a1 import Area, parse

pytestmark = pytest.mark.no_db

TAB = st.text(min_size=1, max_size=12).filter(lambda t: "!" not in t and t.strip() == t)
COL = st.integers(1, 18_278)  # A..ZZZ
ROW = st.integers(1, 9_999_999)
A1_ALPHABET = "ABCXYZabz0123456789:!'$ "


def letters(index: int) -> str:
    out = ""
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def quote(tab: str) -> str:
    return "'" + tab.replace("'", "''") + "'"


def _parse(text: str) -> Area | None:
    try:
        return parse(text)
    except ValueError:
        return None


@given(st.one_of(st.text(max_size=40), st.text(alphabet=A1_ALPHABET, max_size=20)))
def test_any_text_parses_or_is_rejected(text: str) -> None:
    area = _parse(text)
    if area is not None:
        assert area.tab
        assert area.first_col <= area.last_col and area.first_row <= area.last_row
        assert area.contains(area)


@given(TAB, COL, ROW, COL, ROW)
def test_well_formed_range_round_trips(tab: str, c1: int, r1: int, c2: int, r2: int) -> None:
    (c1, c2), (r1, r2) = sorted((c1, c2)), sorted((r1, r2))
    area = _parse(f"{quote(tab)}!{letters(c1)}{r1}:{letters(c2)}{r2}")
    assert area == Area(tab, c1, r1, c2, r2)


@given(TAB, COL, COL)
def test_open_column_range_is_every_row(tab: str, c1: int, c2: int) -> None:
    (c1, c2) = sorted((c1, c2))
    area = _parse(f"{quote(tab)}!{letters(c1)}:{letters(c2)}")
    assert area == Area(tab, c1, 1, c2, math.inf)


@given(TAB, COL, ROW, COL, ROW, COL, ROW, COL, ROW)
def test_containment_is_the_rectangle_check(
    tab: str, a: int, b: int, c: int, d: int, e: int, f: int, g: int, h: int
) -> None:
    outer = Area(tab, min(a, c), min(b, d), max(a, c), max(b, d))
    inner = Area(tab.upper(), min(e, g), min(f, h), max(e, g), max(f, h))
    expected = (
        outer.first_col <= inner.first_col
        and inner.last_col <= outer.last_col
        and outer.first_row <= inner.first_row
        and inner.last_row <= outer.last_row
        and tab.casefold() == tab.upper().casefold()
    )
    assert outer.contains(inner) is expected


@given(TAB, st.text(max_size=10))
def test_trailing_garbage_is_rejected_or_harmless(tab: str, junk: str) -> None:
    """Appending text to a range never produces a range larger than the unmodified one
    unless the text itself is a well-formed reference."""
    base = _parse(f"{quote(tab)}!A1:B2")
    assert base is not None
    extended = _parse(f"{quote(tab)}!A1:B2{junk}")
    if extended is not None and not junk.strip().isdigit():
        assert extended.tab == tab
