"""Caller end to end: consent rules, approval, idempotent calls, and Sheets results (spec section 10)."""

import json
import re
from pathlib import Path
from typing import Any

from agent_runs import assert_result_matches_manifest, launch, prepare

from crewquarters_fake.client import FakePlatformClient
from crewquarters_fake.harness import RunOutcome
from crewquarters_fake.launcher import LaunchHandle

BASIC = {"spreadsheetId": "caller-sheet", "callPollSeconds": 0.05, "timezone": "Asia/Kolkata"}
FULL_NUMBER = re.compile(r"\+1555555\d{4}")


APPROVE = {"choice": "approve"}


def run_caller(
    client: FakePlatformClient,
    tmp_path: Path,
    scenario: str,
    config: dict[str, Any],
    answer: dict[str, Any] | None = None,
    **kw: Any,
) -> RunOutcome:
    """Load the scenario, then register the operator's answer (loading a scenario replaces auto-answers)."""
    manifest, installation = prepare(client, "caller", scenario, config)
    if answer is not None:
        client.add_auto_answer("confirm-calls-v1:*", answer)
    outcome = launch(client, "caller", manifest, installation, tmp_path, **kw)
    if outcome.state == "SUCCEEDED":
        assert_result_matches_manifest(manifest, outcome.result)
    return outcome


def results_tab(client: FakePlatformClient, sheet: str = "caller-sheet") -> list[list[str]]:
    return list(client.state("sheets")[sheet]["Results"])


def assert_no_full_numbers(client: FakePlatformClient, outcome: RunOutcome, sheet: str) -> None:
    assert not FULL_NUMBER.search(json.dumps(outcome.events)), "full number in run events"
    assert not FULL_NUMBER.search(outcome.log), "full number in agent logs"
    assert not FULL_NUMBER.search(json.dumps(outcome.result)), "full number in the result"
    assert not FULL_NUMBER.search(json.dumps(results_tab(client, sheet))), "full number in the results sheet"
    assert not FULL_NUMBER.search(json.dumps(client.state("inputs"))), "full number in the approval request"
    assert client.state("llm") == []


def test_approved_run_calls_only_eligible_rows_and_writes_results(
    fake_client: FakePlatformClient, tmp_path: Path
) -> None:
    outcome = run_caller(fake_client, tmp_path, "caller-basic", BASIC, APPROVE)
    assert outcome.state == "SUCCEEDED", outcome.log
    result = outcome.result
    assert result["operatorDecision"] == "approved"
    assert fake_client.state("calls")["byNumber"] == {"+15555550101": 1, "+15555550103": 1, "+15555550105": 1}
    by_row = {r["row"]: r for r in result["rows"]}
    assert {row: r["skipReason"] for row, r in by_row.items() if r["consent"] == "skipped"} == {
        3: "consent",
        5: "invalid_number",
        7: "status",
        9: "duplicate",
        10: "over_cap",
    }
    assert (by_row[2]["callStatus"], by_row[2]["transcript"]) == ("answered_speech", "Yes, I'll be there")
    assert by_row[4]["callStatus"] == "answered_no_speech"
    assert by_row[6]["callStatus"] == "busy"
    assert all(by_row[row]["sheetWrite"] == "written" for row in (2, 4, 6))
    assert result["summary"] == {
        "called": 3,
        "answered": 2,
        "responsesCaptured": 1,
        "skipped": 5,
        "failed": 0,
    }
    sheet = results_tab(fake_client)
    assert sheet[0] == [
        "source_row",
        "name",
        "phone_masked",
        "call_sid",
        "status",
        "transcript",
        "completed_at",
        "error",
    ]
    assert sheet[1][:3] == ["2", "Asha", "••••0101"]
    assert sheet[1][4:6] == ["answered_speech", "Yes, I'll be there"]
    assert sheet[3][:5][:3] == ["4", "Chen", "••••0103"]
    assert sheet[5][4] == "busy"
    assert sheet[2] == [] and sheet[4] == []
    assert_no_full_numbers(fake_client, outcome, "caller-sheet")


def test_approval_request_previews_masked_recipients_and_exact_count(
    fake_client: FakePlatformClient, tmp_path: Path
) -> None:
    manifest, installation = prepare(fake_client, "caller", "caller-basic", BASIC)
    captured: dict[str, Any] = {}

    def answer_after_review(handle: LaunchHandle, run_id: str) -> None:
        fake_client.wait_for(lambda: bool(fake_client.input_requests(state="pending")), timeout=20)
        [request] = fake_client.input_requests(state="pending")
        captured.update(request)
        fake_client.answer(request["id"], request["version"], {"choice": "approve"})

    outcome = launch(fake_client, "caller", manifest, installation, tmp_path, on_launch=answer_after_review)
    assert outcome.state == "SUCCEEDED", outcome.log
    assert captured["title"] == "Approve 3 automated calls"
    assert captured["key"].startswith("confirm-calls-v1:")
    assert [c["label"] for c in captured["choices"]] == ["Approve 3 calls", "Cancel run"]
    recipients = captured["preview"][0]
    assert [row[2] for row in recipients["rows"]] == ["••••0101", "••••0103", "••••0105"]
    assert "3 automated calls will be placed now" in captured["consequence"]


def test_consent_no_is_never_called(fake_client: FakePlatformClient, tmp_path: Path) -> None:
    run_caller(fake_client, tmp_path, "caller-basic", BASIC, APPROVE)
    assert "+15555550102" not in fake_client.state("calls")["byNumber"]


def test_operator_cancel_places_zero_calls(fake_client: FakePlatformClient, tmp_path: Path) -> None:
    outcome = run_caller(fake_client, tmp_path, "caller-basic", BASIC, {"choice": "cancel"})
    assert outcome.state == "SUCCEEDED", outcome.log
    assert outcome.result["operatorDecision"] == "cancelled"
    assert outcome.result["summary"]["called"] == 0
    assert fake_client.state("calls")["byNumber"] == {}
    assert results_tab(fake_client) == []


def test_failed_sheet_writes_are_retried_without_redialing(
    fake_client: FakePlatformClient, tmp_path: Path
) -> None:
    fake_client.add_fault("broker.sheets.update", "error", count=6, status=503, code="PROVIDER_UNAVAILABLE")
    outcome = run_caller(fake_client, tmp_path, "caller-basic", BASIC, APPROVE)
    assert outcome.state == "SUCCEEDED", outcome.log
    assert all(r["sheetWrite"] == "written" for r in outcome.result["rows"] if r["consent"] == "validated")
    assert set(fake_client.state("calls")["byNumber"].values()) == {1}
    assert any("sheet write" in e["payload"]["message"] for e in outcome.events_of("log"))


def test_dropped_create_call_response_places_one_provider_call(
    fake_client: FakePlatformClient, tmp_path: Path
) -> None:
    fake_client.add_fault("broker.telephony.create", "apply-then-drop", count=3)
    outcome = run_caller(fake_client, tmp_path, "caller-basic", BASIC, APPROVE)
    assert outcome.state == "SUCCEEDED", outcome.log
    assert fake_client.state("calls")["byNumber"] == {"+15555550101": 1, "+15555550103": 1, "+15555550105": 1}


def test_interrupted_run_retries_without_redialing(fake_client: FakePlatformClient, tmp_path: Path) -> None:
    manifest, installation = prepare(fake_client, "caller", "caller-basic", BASIC)
    fake_client.add_auto_answer("confirm-calls-v1:*", APPROVE)

    def kill_after_first_row(handle: LaunchHandle, run_id: str) -> None:
        fake_client.wait_for(
            lambda: len(results_tab(fake_client)) >= 2 and bool(results_tab(fake_client)[1]), timeout=30
        )
        handle.kill()

    first = launch(fake_client, "caller", manifest, installation, tmp_path, on_launch=kill_after_first_row)
    assert first.state == "INTERRUPTED"
    fake_client.retry(first.run["id"])
    second = launch(fake_client, "caller", manifest, None, tmp_path, run_id=first.run["id"])
    assert second.state == "SUCCEEDED", second.log
    assert fake_client.state("calls")["byNumber"] == {"+15555550101": 1, "+15555550103": 1, "+15555550105": 1}
    assert [r["row"] for r in second.result["rows"] if r["callSid"]] == [2, 4, 6]
    sheet = results_tab(fake_client)
    assert [sheet[i][0] for i in (1, 3, 5)] == ["2", "4", "6"]
    requested = [e for e in second.events if e["type"] == "input.requested"]
    assert len(requested) == 1, "the retried attempt reused the stored approval"


def test_every_call_state_is_visible(fake_client: FakePlatformClient, tmp_path: Path) -> None:
    config = {"spreadsheetId": "status-sheet", "maxCalls": 6, "callPollSeconds": 0.05, "timezone": "UTC"}
    outcome = run_caller(fake_client, tmp_path, "caller-statuses", config)
    assert outcome.state == "SUCCEEDED", outcome.log
    statuses = {r["name"]: (r["callStatus"], r["error"]) for r in outcome.result["rows"]}
    assert statuses == {
        "Speech": ("answered_speech", None),
        "Silent": ("answered_no_speech", None),
        "Busy": ("busy", None),
        "NoAnswer": ("no_answer", None),
        "Failed": ("failed", "carrier-rejected"),
        "Unverified": ("failed", "unverified-number"),
    }
    assert outcome.result["summary"] == {
        "called": 6,
        "answered": 2,
        "responsesCaptured": 1,
        "skipped": 0,
        "failed": 2,
    }
    assert_no_full_numbers(fake_client, outcome, "status-sheet")


def test_call_still_ringing_at_the_timeout_is_reported_as_timeout(
    fake_client: FakePlatformClient, tmp_path: Path
) -> None:
    config = {
        "spreadsheetId": "status-sheet",
        "maxCalls": 1,
        "callPollSeconds": 0.5,
        "callTimeoutSeconds": 10,
        "timezone": "UTC",
    }
    outcome = run_caller(fake_client, tmp_path, "caller-ringing", config, timeout=60)
    assert outcome.state == "SUCCEEDED", outcome.log
    [row] = [r for r in outcome.result["rows"] if r["consent"] == "validated"]
    assert (row["callStatus"], row["error"]) == ("timeout", "call still active at the timeout")
    assert outcome.result["summary"]["failed"] == 1


def test_no_eligible_rows_needs_no_approval(fake_client: FakePlatformClient, tmp_path: Path) -> None:
    config = {**BASIC, "inputRange": "Contacts!A3:D3"}
    outcome = run_caller(fake_client, tmp_path, "caller-basic", config)
    assert outcome.state == "SUCCEEDED", outcome.log
    assert outcome.result["operatorDecision"] == "not_required"
    assert outcome.events_of("input.requested") == []
