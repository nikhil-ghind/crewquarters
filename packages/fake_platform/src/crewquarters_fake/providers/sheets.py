"""Google Sheets values API stand-in (get, update, append)."""

from __future__ import annotations

import copy
import dataclasses
from typing import Any

from crewquarters_fake.errors import ApiError
from crewquarters_fake.providers.a1 import A1Range, format_range, parse_range


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


def _trim(rows: list[list[str]]) -> list[list[str]]:
    trimmed = []
    for row in rows:
        row = list(row)
        while row and row[-1] == "":
            row.pop()
        trimmed.append(row)
    while trimmed and not trimmed[-1]:
        trimmed.pop()
    return trimmed


class SheetsProvider:
    def __init__(self) -> None:
        self.books: dict[str, dict[str, list[list[str]]]] = {}

    def load(self, spreadsheets: dict[str, dict[str, list[list[Any]]]]) -> None:
        self.books = {
            sheet_id: {
                tab: [[cell_text(c) for c in row] for row in rows or []]
                for tab, rows in tabs.items()
            }
            for sheet_id, tabs in spreadsheets.items()
        }

    def snapshot(self) -> dict[str, dict[str, list[list[str]]]]:
        return copy.deepcopy(self.books)

    def _locate(self, spreadsheet_id: str, a1: str) -> tuple[list[list[str]], A1Range]:
        try:
            parsed = parse_range(a1)
        except ValueError as exc:
            raise ApiError(400, "INVALID_REQUEST", str(exc)) from exc
        book = self.books.get(spreadsheet_id)
        if book is None:
            raise ApiError(404, "NOT_FOUND", f"spreadsheet {spreadsheet_id} not found")
        # Google matches tab names without regard to case.
        tab = next((t for t in book if t.casefold() == parsed.tab.casefold()), None)
        if tab is None:
            raise ApiError(404, "NOT_FOUND", f"sheet {parsed.tab} not found")
        return book[tab], dataclasses.replace(parsed, tab=tab)

    def get(self, spreadsheet_id: str, a1: str) -> dict[str, Any]:
        rows, rng = self._locate(spreadsheet_id, a1)
        first = (rng.start_row or 1) - 1
        last = rng.end_row if rng.end_row is not None else len(rows)
        end_col = rng.end_col + 1 if rng.end_col is not None else None
        values = [row[rng.start_col : end_col] for row in rows[first:last]]
        return {"range": a1, "values": _trim(values)}

    def _write(
        self, rows: list[list[str]], rng: A1Range, row_index: int, values: list[list[Any]]
    ) -> dict[str, Any]:
        width = max((len(v) for v in values), default=0)
        for offset, row_values in enumerate(values):
            target = row_index + offset
            while len(rows) <= target:
                rows.append([])
            row = rows[target]
            needed = rng.start_col + len(row_values)
            row.extend([""] * max(0, needed - len(row)))
            for col, value in enumerate(row_values):
                row[rng.start_col + col] = cell_text(value)
        updated = format_range(
            rng.tab,
            rng.start_col,
            row_index + 1,
            rng.start_col + max(width, 1) - 1,
            row_index + len(values),
        )
        return {"updatedRange": updated, "updatedRows": len(values)}

    def update(self, spreadsheet_id: str, a1: str, values: list[list[Any]]) -> dict[str, Any]:
        rows, rng = self._locate(spreadsheet_id, a1)
        return self._write(rows, rng, (rng.start_row or 1) - 1, values)

    def append(self, spreadsheet_id: str, a1: str, values: list[list[Any]]) -> dict[str, Any]:
        rows, rng = self._locate(spreadsheet_id, a1)
        end_col = rng.end_col + 1 if rng.end_col is not None else None
        last = (rng.start_row or 1) - 2
        for index in range((rng.start_row or 1) - 1, len(rows)):
            if any(cell != "" for cell in rows[index][rng.start_col : end_col]):
                last = index
        return self._write(rows, rng, last + 1, values)
