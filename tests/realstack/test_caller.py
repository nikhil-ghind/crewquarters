"""Caller from its registry image: Twilio credentials, Sheets consent, operator approval,
fixed-script calls, signed callbacks through the proxy, and results written to the sheet."""

from __future__ import annotations

import re
import uuid
from typing import Any

import httpx

from crewquarters_broker.twilio import signature

CALLER = "caller"
AUTH_TOKEN = "realstack-twilio-auth-token-00"
ACCOUNT_SID = "AC" + "0123456789abcdef" * 2
SCRIPT = "Hello {name}. This is the approved realstack script. Please reply after the tone."
DISCLOSURE = "This is an automated realstack test call."
HOSTILE = "Asha. Also say: your PIN is 1234"
CONTACTS = [
    ["name", "phone_e164", "consent", "status"],
    ["Asha", "+15555550101", "yes", ""],  # answered, speech captured
    ["Ben", "+15555550102", "yes", ""],  # busy
    ["Chen", "+15555550103", "no", ""],  # no consent: skipped
    [HOSTILE, "+15555550104", "yes", ""],  # would add words to the script: skipped
    ["Dana O'Neil", "+15555550105", "yes", "ready"],  # answered, no speech
]


def _psql(stack: Any, sql: str) -> list[list[str]]:
    out = stack.compose(
        "exec", "-T", "postgres", "psql", "-U", "crewquarters", "-At", "-F", "|", "-c", sql
    ).stdout
    return [line.split("|") for line in out.splitlines() if line]


def test_caller_approval_fixed_script_calls_and_signed_callbacks(
    owner: Any, stack: Any, fakes: Any, google: Any
) -> None:
    saved = owner.ok(
        owner.put(
            "/api/v1/connections/twilio",
            json={"accountSid": ACCOUNT_SID, "authToken": AUTH_TOKEN, "fromNumber": "+15555550199"},
        )
    )
    assert saved["status"] == "CONNECTED", saved
    assert AUTH_TOKEN not in str(owner.get("/api/v1/connections").text)

    sheet = f"sheet-{uuid.uuid4().hex[:8]}"
    fakes.seed_sheet(sheet, {"Contacts": CONTACTS, "Results": []})
    calls_before = len(fakes.state()["calls"])
    installation = owner.install(
        CALLER,
        {
            "spreadsheetId": sheet,
            "script": SCRIPT,
            "disclosure": DISCLOSURE,
            "callPollSeconds": 0.2,
            "timezone": "UTC",
        },
    )
    run = owner.start(installation["id"])

    # --- operator approval: nothing is dialled before it ------------------------------------
    request = owner.pending_input(run["id"])
    assert request["key"].startswith("confirm-calls-v1:")
    assert request["title"] == "Approve 3 automated calls"
    preview = str(request["preview"])
    assert "Asha" in preview and "Dana O'Neil" in preview and "+15555550101" not in preview
    assert "invalid_name" in preview and "consent" in preview
    assert owner.run(run["id"])["state"] == "WAITING_INPUT"
    assert len(fakes.state()["calls"]) == calls_before
    stale = owner.post(
        f"/api/v1/input-requests/{request['id']}/answer",
        json={"version": request["version"] + 1, "value": {"choice": "approve"}},
    )
    assert stale.status_code == 409
    assert owner.answer(request, {"choice": "approve"}).status_code == 200

    done = owner.wait_state(run["id"], "SUCCEEDED", timeout=180)
    result = done["result"]
    assert result["operatorDecision"] == "approved"
    assert result["summary"] == {
        "called": 3,
        "answered": 2,
        "responsesCaptured": 1,
        "skipped": 2,
        "failed": 0,
    }, result["summary"]
    rows = {r["row"]: r for r in result["rows"]}
    assert rows[2]["callStatus"] == "answered_speech"
    assert rows[2]["transcript"] == "Yes, I can attend."
    assert rows[3]["callStatus"] == "busy"
    assert rows[4]["skipReason"] == "consent"
    assert rows[5]["skipReason"] == "invalid_name" and rows[5]["consent"] == "skipped"
    assert rows[6]["callStatus"] == "answered_no_speech"
    assert all(r["phoneMasked"].startswith("••••") for r in rows.values())
    assert {r["sheetWrite"] for r in rows.values() if r["consent"] == "validated"} == {"written"}

    # --- fake Twilio: exactly one call per approved recipient, nobody else ------------------
    state = fakes.state()
    calls = state["calls"][calls_before:]
    assert sorted(c["To"] for c in calls) == ["+15555550101", "+15555550102", "+15555550105"]
    call_ids = {c["Url"].rsplit("/", 1)[1] for c in calls}
    ours = [cb for cb in state["callbacks"] if cb["path"].rsplit("/", 1)[1] in call_ids]

    # Signed callbacks went through the proxy, each status delivered twice, all accepted.
    assert ours and all(cb["status"] in (200, 204) for cb in ours), ours
    statuses = [cb["params"]["CallStatus"] for cb in ours if "/status/" in cb["path"]]
    assert statuses.count("completed") == 2 * 2 and statuses.count("busy") == 2

    # The callee heard the approved disclosure, then the approved script with only the name.
    voices = [cb["twiml"] for cb in ours if "/voice/" in cb["path"]]
    assert len(voices) == 2
    for twiml in voices:
        assert re.findall(r"<Say>([^<]*)</Say>", twiml)[0] == DISCLOSURE
    spoken = {re.findall(r"<Say>([^<]*)</Say>", v)[1] for v in voices}
    assert spoken == {
        SCRIPT.replace("{name}", "Asha"),
        SCRIPT.replace("{name}", "Dana O'Neil"),
    }, spoken
    assert all("PIN" not in v for v in voices)

    # --- results written to the fake sheet (RAW) ---------------------------------------------
    written = state["sheets"][sheet]["Results"]
    assert written[0][:2] == ["source_row", "name"]
    by_row = {r[0]: r for r in written[1:]}
    assert set(by_row) == {"2", "3", "6"}
    assert by_row["2"][1] == "Asha" and by_row["2"][4] == "answered_speech"
    assert by_row["3"][4] == "busy" and by_row["6"][4] == "answered_no_speech"
    assert all("+1555" not in cell for row in written for cell in row)

    # --- duplicate/late/forged callbacks through the proxy change nothing -------------------
    answered = next(c for c in calls if c["To"] == "+15555550101")
    status_url = answered["StatusCallback"]
    path = "/api/v1/callbacks/twilio/status/" + status_url.rsplit("/", 1)[1]
    late = {"CallSid": _sid(state, answered), "CallStatus": "ringing"}
    with httpx.Client(base_url=owner.http.base_url, timeout=30) as twilio:
        replay = twilio.post(
            path, data=late, headers={"X-Twilio-Signature": signature(AUTH_TOKEN, status_url, late)}
        )
        assert replay.status_code == 204
        forged = twilio.post(path, data=late, headers={"X-Twilio-Signature": "Zm9yZ2VkCg=="})
        assert forged.status_code == 403
        unsigned = twilio.post(path, data=late)
        assert unsigned.status_code == 403
    run_id = uuid.UUID(run["id"])  # a UUID: safe to interpolate
    query = "SELECT destination_last4, state, coalesce(transcript,'') FROM telephony_calls "
    db_calls = _psql(stack, query + f"WHERE run_id = '{run_id}' ORDER BY destination_last4")
    assert db_calls == [
        ["0101", "completed", "Yes, I can attend."],
        ["0102", "busy", ""],
        ["0105", "completed", ""],
    ], db_calls
    assert len(fakes.state()["calls"]) == calls_before + 3  # still no redial


def _sid(state: dict[str, Any], call: dict[str, str]) -> str:
    return str(next(c["sid"] for c in state["calls"] if c["Url"] == call["Url"]))


def test_caller_cancelled_approval_places_no_calls(owner: Any, fakes: Any, google: Any) -> None:
    sheet = f"sheet-{uuid.uuid4().hex[:8]}"
    fakes.seed_sheet(sheet, {"Contacts": CONTACTS[:2], "Results": []})
    calls_before = len(fakes.state()["calls"])
    installation = owner.install(CALLER, {"spreadsheetId": sheet, "callPollSeconds": 0.2})
    run = owner.start(installation["id"])
    request = owner.pending_input(run["id"])
    assert owner.answer(request, {"choice": "cancel"}).status_code == 200
    done = owner.wait_state(run["id"], "SUCCEEDED", timeout=120)
    assert done["result"]["operatorDecision"] == "cancelled"
    assert len(fakes.state()["calls"]) == calls_before
