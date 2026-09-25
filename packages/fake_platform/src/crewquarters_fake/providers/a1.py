"""A1 range notation (``Tab!A2:D``) used by the Sheets fake."""

from __future__ import annotations

import re
from dataclasses import dataclass

_CELL_RE = re.compile(r"^([A-Z]+)(\d+)?$")


@dataclass(frozen=True)
class A1Range:
    tab: str
    start_col: int
    start_row: int | None
    end_col: int | None
    end_row: int | None


def col_index(letters: str) -> int:
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def col_letters(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _split_tab(text: str) -> tuple[str, str | None]:
    if text.startswith("'"):
        end = 1
        name = ""
        while end < len(text):
            if text[end] == "'" and text[end + 1 : end + 2] == "'":
                name += "'"
                end += 2
            elif text[end] == "'":
                break
            else:
                name += text[end]
                end += 1
        rest = text[end + 1 :]
        if not rest:
            return name, None
        if not rest.startswith("!"):
            raise ValueError(f"invalid range {text!r}")
        return name, rest[1:]
    if "!" in text:
        tab, ref = text.split("!", 1)
        return tab, ref
    return text, None


def parse_range(text: str) -> A1Range:
    tab, ref = _split_tab(text.strip())
    if not tab:
        raise ValueError(f"invalid range {text!r}: missing sheet name")
    if ref is None:
        return A1Range(tab, 0, None, None, None)
    parts = ref.split(":")
    if not ref or len(parts) > 2:
        raise ValueError(f"invalid range {text!r}")
    start = _CELL_RE.match(parts[0])
    if start is None:
        raise ValueError(f"invalid range {text!r}")
    start_col = col_index(start.group(1))
    start_row = int(start.group(2)) if start.group(2) else None
    if len(parts) == 1:
        return A1Range(tab, start_col, start_row, start_col, start_row)
    end = _CELL_RE.match(parts[1])
    if end is None:
        raise ValueError(f"invalid range {text!r}")
    end_row = int(end.group(2)) if end.group(2) else None
    return A1Range(tab, start_col, start_row, col_index(end.group(1)), end_row)


def _quote(tab: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_]+", tab):
        return tab
    return "'" + tab.replace("'", "''") + "'"


def format_range(tab: str, start_col: int, start_row: int, end_col: int, end_row: int) -> str:
    start = f"{col_letters(start_col)}{start_row}"
    end = f"{col_letters(end_col)}{end_row}"
    return f"{_quote(tab)}!{start}" if start == end else f"{_quote(tab)}!{start}:{end}"
