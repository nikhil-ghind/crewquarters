"""Google Sheets client. ``append_values`` is never retried; prefer ``update_values``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crewquarters._transport import BrokerClient

CellValue = str | int | float | bool | None


@dataclass(frozen=True)
class UpdateResult:
    updated_range: str
    updated_rows: int


class SheetsClient:
    def __init__(self, transport: BrokerClient) -> None:
        self._transport = transport

    async def get_values(self, spreadsheet_id: str, range_: str) -> list[list[str]]:
        data = await self._transport.request(
            "POST",
            "/google/sheets/values:get",
            operation="sheets.get",
            idempotent=True,
            json={"spreadsheetId": spreadsheet_id, "range": range_},
        )
        return [[str(cell) for cell in row] for row in data.get("values", [])]

    async def update_values(
        self, spreadsheet_id: str, range_: str, values: list[list[CellValue]]
    ) -> UpdateResult:
        return await self._write(
            "/google/sheets/values:update", "sheets.update", True, spreadsheet_id, range_, values
        )

    async def append_values(
        self, spreadsheet_id: str, range_: str, values: list[list[CellValue]]
    ) -> UpdateResult:
        return await self._write(
            "/google/sheets/values:append", "sheets.append", False, spreadsheet_id, range_, values
        )

    async def _write(
        self,
        path: str,
        operation: str,
        idempotent: bool,
        spreadsheet_id: str,
        range_: str,
        values: list[list[CellValue]],
    ) -> UpdateResult:
        body: dict[str, Any] = {"spreadsheetId": spreadsheet_id, "range": range_, "values": values}
        data = await self._transport.request(
            "POST", path, operation=operation, idempotent=idempotent, json=body
        )
        return UpdateResult(str(data["updatedRange"]), int(data["updatedRows"]))
