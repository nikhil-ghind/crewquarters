"""The fake platform scopes Sheets calls exactly as the real broker does: the same ranges parse
(or fail) the same way, and the same config and request give the same error code, status and
details. The HTTP cases repeat ``services/capability_broker/tests/test_broker_google.py``."""

from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import yaml
from fake_helpers import SDK, manifest, start

from crewquarters_fake.broker import sheet_scope
from crewquarters_fake.errors import ApiError

SHEET = "sheet-1"
# The caller's defaults, as in the broker's test config.
CONFIG = {"spreadsheetId": SHEET, "inputRange": "Contacts!A2:D", "resultRange": "Results!A:H"}

RANGES = [
    "Contacts!A2:D",
    "Contacts!B5:C9",
    "contacts!a2:d2",
    "Contacts!A1:D",
    "Contacts",
    "'Other tab'!A2:D",
    "'It''s'!B3",
    "Results!A:H",
    "Results!A:Z",
    "Results!H3",
    "Results!3:5",
    "Results!A",
    "A1",
    "A2:D",
    "A:H",
    "Contacts!A2:D!B",
    "Contacts!2A",
    "Contacts!A1:B2:C3",
    "Contacts!B2:A1",
    "Contacts!AAAA1",
    "Contacts!A0",
    "Contacts!",
    "!A1",
    "'x!A1",
    "''!A1",
    "",
    "  Contacts!A2  ",
]

# (operation, range, allowed) from test_sheets_are_scoped_to_the_configured_ranges.
SCOPED = [
    ("get", "Contacts!A2:D", True),
    ("get", "Contacts!B5:C9", True),
    ("get", "contacts!a2:d2", True),
    ("get", "Contacts!A1:D", False),
    ("get", "Contacts!A2:E", False),
    ("get", "Contacts", False),
    ("get", "Results!A2:D", False),
    ("get", "'Other tab'!A2:D", False),
    ("update", "Results!A1:H1", True),
    ("update", "Results!A7:H7", True),
    ("update", "Results!H3", True),
    ("update", "Results!A1:I1", False),
    ("update", "Contacts!A2:D2", False),
    ("update", "Results", False),
    ("append", "Results!A:H", True),
    ("append", "Results!A:Z", False),
    ("append", "Contacts!A:D", False),
]
KEYS = {"get": "inputRange", "update": "resultRange", "append": "resultRange"}
# The voice caller's defaults: its result rows are ten columns wide, and it never writes a
# contact's status cell (that stays the owner's; docs/voice/README.md).
VOICE_CONFIG = {"spreadsheetId": SHEET, "inputRange": "Contacts!A2:D", "resultRange": "Results!A:J"}
VOICE_SCOPED = [
    ("inputRange", "Contacts!A2:D", True),
    ("resultRange", "Results!A1:J1", True),
    ("resultRange", "Results!A4:J4", True),
    ("resultRange", "Results!A4:K4", False),
    ("resultRange", "Contacts!D2", False),
    ("resultRange", "Contacts!A2:D2", False),
]


def _broker_parse(text: str) -> tuple[Any, ...] | None:
    from crewquarters_broker import a1

    try:
        area = a1.parse(text)
    except ValueError:
        return None
    return (area.tab, area.first_col, area.first_row, area.last_col, area.last_row)


def _fake_parse(text: str) -> tuple[Any, ...] | None:
    try:
        area = sheet_scope.parse(text)
    except ValueError:
        return None
    return (area.tab, area.first_col, area.first_row, area.last_col, area.last_row)


@pytest.mark.parametrize("text", RANGES)
def test_ranges_parse_like_the_real_broker(text: str) -> None:
    assert _fake_parse(text) == _broker_parse(text)


def _broker_outcome(config: dict[str, Any], key: str, requested: str) -> tuple[Any, ...]:
    from crewquarters_broker.agent_api import _within
    from crewquarters_broker.auth import Grant
    from crewquarters_shared.errors import PlatformError

    grant = Grant(claims=cast(Any, None), run={"config": config}, capabilities=frozenset())
    try:
        grant.configured("spreadsheetId", SHEET)
        _within(grant, key, requested)
    except PlatformError as exc:
        return (exc.status_code, exc.code, exc.message, exc.details)
    return ("ok",)


def _fake_outcome(config: dict[str, Any], key: str, requested: str) -> tuple[Any, ...]:
    try:
        sheet_scope.configured(config, "spreadsheetId", SHEET)
        sheet_scope.within(config, key, requested)
    except ApiError as exc:
        return (exc.status, exc.code, exc.message, exc.details)
    return ("ok",)


CONFIGS = [
    CONFIG,
    {"spreadsheetId": SHEET},  # no ranges
    {**CONFIG, "inputRange": "A2:D", "resultRange": "Results!Z:A"},  # invalid ranges
    {**CONFIG, "inputRange": "", "resultRange": 7},
    {**CONFIG, "spreadsheetId": "someone-elses-sheet"},
    {"inputRange": "Contacts!A2:D"},  # no spreadsheet
    VOICE_CONFIG,
]


@pytest.mark.parametrize("config", CONFIGS)
@pytest.mark.parametrize("key", ["inputRange", "resultRange"])
@pytest.mark.parametrize("requested", RANGES)
def test_scope_errors_match_the_real_broker(
    config: dict[str, Any], key: str, requested: str
) -> None:
    assert _fake_outcome(config, key, requested) == _broker_outcome(config, key, requested)


# --- The same outcomes over HTTP ----------------------------------------------------------


def _manifest(**config: str) -> dict[str, Any]:
    m = manifest(connectors={"google": ["spreadsheets"], "twilio": []})
    properties = m["spec"]["configurationSchema"]["properties"]
    for key, value in config.items():
        properties[key] = {"type": "string", "default": value}
    return m


async def _headers(api: httpx.AsyncClient, root: Path, **config: str) -> dict[str, str]:
    (root / "scenario.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "sheets-scope",
                "timezone": "UTC",
                "sheets": {
                    "spreadsheets": {
                        SHEET: {"Contacts": [["name"], ["Asha"]], "Results": [], "Other tab": []}
                    }
                },
            }
        )
    )
    r = await api.post("/fake/v1/scenarios/load", json={"path": str(root)})
    assert r.status_code == 200, r.text
    return (await start(api, _manifest(**config))).headers


def _body(cells: str, operation: str, sheet: str = SHEET) -> dict[str, Any]:
    body: dict[str, Any] = {"spreadsheetId": sheet, "range": cells}
    if operation != "get":
        body["values"] = [["x"]]
    return body


@pytest.mark.parametrize(("operation", "cells", "allowed"), SCOPED)
async def test_sheets_are_scoped_to_the_configured_ranges(
    api: httpx.AsyncClient, tmp_path: Path, operation: str, cells: str, allowed: bool
) -> None:
    headers = await _headers(api, tmp_path, **CONFIG)
    resp = await api.post(
        f"{SDK}/google/sheets/values:{operation}", headers=headers, json=_body(cells, operation)
    )
    if allowed:
        assert resp.status_code == 200, resp.text
    else:
        assert resp.status_code == 403 and resp.json()["error"]["code"] == "PERMISSION_DENIED"
        assert resp.json()["error"]["details"] == {"key": KEYS[operation]}


@pytest.mark.parametrize("cells", ["A1", "A2:D", "Contacts!A2:D!B", "Contacts!2A", "'x!A1"])
async def test_sheets_ranges_must_be_a1_with_a_tab(
    api: httpx.AsyncClient, tmp_path: Path, cells: str
) -> None:
    headers = await _headers(api, tmp_path, **CONFIG)
    resp = await api.post(
        f"{SDK}/google/sheets/values:get", headers=headers, json=_body(cells, "get")
    )
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "INVALID_REQUEST"


async def test_sheets_need_configured_ranges(api: httpx.AsyncClient, tmp_path: Path) -> None:
    headers = await _headers(api, tmp_path, spreadsheetId=SHEET)
    for operation, key in (("get", "inputRange"), ("append", "resultRange")):
        resp = await api.post(
            f"{SDK}/google/sheets/values:{operation}",
            headers=headers,
            json=_body("Results!A1", operation),
        )
        assert resp.status_code == 409 and resp.json()["error"]["code"] == "NEEDS_CONFIGURATION"
        assert resp.json()["error"]["details"] == {"key": key}


async def test_sheets_need_configured_spreadsheet(api: httpx.AsyncClient, tmp_path: Path) -> None:
    headers = await _headers(api, tmp_path)
    resp = await api.post(
        f"{SDK}/google/sheets/values:get", headers=headers, json=_body("A1", "get")
    )
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "NEEDS_CONFIGURATION"
    assert resp.json()["error"]["details"] == {"key": "spreadsheetId"}


async def test_sheets_use_configured_spreadsheet_only(
    api: httpx.AsyncClient, tmp_path: Path
) -> None:
    headers = await _headers(api, tmp_path, **CONFIG)
    resp = await api.post(
        f"{SDK}/google/sheets/values:append",
        headers=headers,
        json=_body("Results!A:H", "append", sheet="someone-elses-sheet"),
    )
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "PERMISSION_DENIED"
    assert resp.json()["error"]["details"] == {"key": "spreadsheetId"}


@pytest.mark.parametrize(("key", "requested", "allowed"), VOICE_SCOPED)
def test_the_voice_callers_writes_are_scoped_like_the_real_broker(
    key: str, requested: str, allowed: bool
) -> None:
    outcome = _broker_outcome(VOICE_CONFIG, key, requested)
    assert _fake_outcome(VOICE_CONFIG, key, requested) == outcome
    if allowed:
        assert outcome == ("ok",)
    else:
        assert outcome[:2] == (403, "PERMISSION_DENIED") and outcome[3] == {"key": key}
