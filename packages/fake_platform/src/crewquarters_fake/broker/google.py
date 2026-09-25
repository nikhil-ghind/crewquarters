"""Broker Gmail and Sheets routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from crewquarters_fake.broker import sheet_scope
from crewquarters_fake.broker.audit import audited, require_connection
from crewquarters_fake.broker.auth import RunAuth, require, run_auth

router = APIRouter()


class RangeIn(BaseModel):
    spreadsheetId: str
    range: str = Field(min_length=1, max_length=200)


class WriteIn(RangeIn):
    values: list[list[str | int | float | bool | None]]


@router.get("/google/gmail/messages")
async def gmail_list(
    request: Request,
    q: str = "",
    maxResults: int = Query(100, ge=1, le=500),
    pageToken: str | None = None,
    labelIds: list[str] | None = Query(None),
    auth: RunAuth = Depends(run_auth),
) -> dict[str, Any]:
    require(auth, "google.gmail.readonly", "broker.gmail.list")
    require_connection(auth, "google")

    async def call() -> dict[str, Any]:
        return auth.store.gmail.list(q, maxResults, pageToken, labelIds)

    return await audited(auth, request, "google.gmail", "broker.gmail.list", call)


@router.get("/google/gmail/messages/{message_id}")
async def gmail_get(
    message_id: str, request: Request, auth: RunAuth = Depends(run_auth)
) -> dict[str, Any]:
    require(auth, "google.gmail.readonly", "broker.gmail.get")
    require_connection(auth, "google")

    async def call() -> dict[str, Any]:
        return auth.store.gmail.get(message_id)

    return await audited(auth, request, "google.gmail", "broker.gmail.get", call)


def _scoped(auth: RunAuth, key: str, body: RangeIn) -> None:
    """Like the real broker: only the configured spreadsheet, reads within ``inputRange`` and
    writes within ``resultRange``, checked before the connection is used."""
    sheet_scope.configured(auth.installation.config, "spreadsheetId", body.spreadsheetId)
    sheet_scope.within(auth.installation.config, key, body.range)


@router.post("/google/sheets/values:get")
async def sheets_get(
    body: RangeIn, request: Request, auth: RunAuth = Depends(run_auth)
) -> dict[str, Any]:
    require(auth, "google.spreadsheets", "broker.sheets.get")
    _scoped(auth, "inputRange", body)
    require_connection(auth, "google")

    async def call() -> dict[str, Any]:
        return auth.store.sheets.get(body.spreadsheetId, body.range)

    return await audited(auth, request, "google.spreadsheets", "broker.sheets.get", call)


@router.post("/google/sheets/values:update")
async def sheets_update(
    body: WriteIn, request: Request, auth: RunAuth = Depends(run_auth)
) -> dict[str, Any]:
    require(auth, "google.spreadsheets", "broker.sheets.update")
    _scoped(auth, "resultRange", body)
    require_connection(auth, "google")

    async def call() -> dict[str, Any]:
        return auth.store.sheets.update(body.spreadsheetId, body.range, body.values)

    return await audited(auth, request, "google.spreadsheets", "broker.sheets.update", call)


@router.post("/google/sheets/values:append")
async def sheets_append(
    body: WriteIn, request: Request, auth: RunAuth = Depends(run_auth)
) -> dict[str, Any]:
    require(auth, "google.spreadsheets", "broker.sheets.append")
    _scoped(auth, "resultRange", body)
    require_connection(auth, "google")

    async def call() -> dict[str, Any]:
        return auth.store.sheets.append(body.spreadsheetId, body.range, body.values)

    return await audited(auth, request, "google.spreadsheets", "broker.sheets.append", call)
