"""The whole run against the fake platform (sheets, approval, action keys, offline voice calls),
with a scripted CallRunner standing in for the audio conversation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from caller_agent.rows import ContactRow
from crewquarters import RunContext
from crewquarters_fake.client import FakePlatformClient
from crewquarters_fake.contracts import load_manifest
from crewquarters_fake.server import BackgroundServer
from voice_caller.agent import build_agent
from voice_caller.config import VoiceCallerConfig
from voice_caller.session import CallReport
from voice_caller.transcript import Transcript

AGENT_DIR = Path(__file__).resolve().parents[1]
SHEET = "voice-sheet"
CONFIG = {"spreadsheetId": SHEET, "organization": "Acme Dental", "purpose": "confirm appointments"}
CONTACTS = [
    ["name", "phone_e164", "consent", "status"],
    ["Asha Rao", "+15555550101", "yes", ""],
    ["Ben Ortiz", "+15555550102", "yes", ""],
    ["Chen Li", "+15555550103", "yes", ""],
    ["Dee Park", "+15555550104", "no", ""],
]
CALLEES = {
    "+15555550101": {"outcome": "answer", "ringSeconds": 0.05},
    "+15555550102": {"outcome": "no-answer", "ringSeconds": 0.05},
    "+15555550103": {"outcome": "answer", "ringSeconds": 0.05},
}
OUTCOME_RULE = {
    "name": "outcome",
    "match": {"schemaTitle": "CallOutcome"},
    "respond": {
        "json": {"disposition": "completed", "interest": "high", "notes": "Keeps Tuesday."}
    },
}


class ScriptedRunner:
    """Waits for the offline call to settle, then reports a scripted conversation."""

    def __init__(self, lines: dict[int, list[str]], errors: dict[int, str] | None = None) -> None:
        self.lines, self.errors = lines, errors or {}
        self.prepared = False
        self.calls: list[int] = []

    async def prepare(self, ctx: RunContext[Any], config: VoiceCallerConfig) -> None:
        self.prepared = True

    async def run_call(
        self, ctx: RunContext[Any], call: Any, contact: ContactRow, config: VoiceCallerConfig
    ) -> CallReport:
        import asyncio

        self.calls.append(contact.row)
        state = call.state
        for _ in range(100):
            current = await ctx.voice.get(call.id)
            state = current.state
            if state not in {"dialing", "ringing"}:
                break
            await asyncio.sleep(0.02)
        if state != "answered":
            return CallReport(call.id, False, state, state)
        transcript = Transcript()
        transcript.add(
            "agent", "Hi, this is Sam, an automated AI assistant calling for Acme Dental."
        )
        for line in self.lines.get(contact.row, []):
            transcript.add("callee", line)
        ended = await ctx.voice.hangup(call.id)
        return CallReport(
            call.id,
            True,
            "error" if contact.row in self.errors else "agent_ended",
            ended.state,
            transcript,
            ended.duration_seconds,
            self.errors.get(contact.row),
        )


@pytest.fixture
def platform(tmp_path: Path) -> Any:
    from crewquarters_fake.app import create_app
    from crewquarters_fake.settings import FakeSettings

    with BackgroundServer(create_app(FakeSettings(heartbeat_seconds=0.2))) as server:
        yield server


def seed(
    client: FakePlatformClient,
    tmp_path: Path,
    answer: str = "approve",
    rows: list[list[str]] | None = None,
) -> None:
    scenario = {
        "name": "voice-run",
        "timezone": "UTC",
        "inputs": {
            "autoAnswers": [{"keyPattern": "confirm-voice-calls-v1:*", "value": {"choice": answer}}]
        },
        "sheets": {"spreadsheets": {SHEET: {"Contacts": rows or CONTACTS, "Results": []}}},
        "voice": {"callees": CALLEES},
        "llm": {"rules": [OUTCOME_RULE]},
    }
    (tmp_path / "scenario.yaml").write_text(yaml.safe_dump(scenario))
    client.load_scenario(str(tmp_path))


def start_run(client: FakePlatformClient, url: str) -> tuple[str, str]:
    manifest = load_manifest(AGENT_DIR / "manifest.yaml")
    client.register_manifest(manifest)
    installation = client.install(
        "voice-call-center", "0.1.0", CONFIG, manifest["spec"]["permissions"]
    )
    run = client.create_run(installation["id"])
    dispatch = client.dispatch(run["id"], url)
    return run["id"], dispatch["env"]["PLATFORM_RUN_TOKEN"]


async def execute(
    platform: BackgroundServer,
    runner: ScriptedRunner,
    prime: Callable[[str, str], None] | None = None,
) -> tuple[dict[str, Any], FakePlatformClient]:
    client = FakePlatformClient(platform.url)
    run_id, token = start_run(client, platform.url)
    if prime is not None:
        prime(run_id, token)
    code = await build_agent(runner).execute(broker_url=platform.url, token=token, run_id=run_id)
    run = client.get_run(run_id)
    assert code == (0 if run["state"] == "SUCCEEDED" else 1), run["error"]
    assert run["state"] == "SUCCEEDED", run["error"]
    return run["result"], client


def sheet(client: FakePlatformClient, tab: str) -> list[list[str]]:
    return client.state("sheets")[SHEET][tab]


async def test_answered_unanswered_and_dnc_calls_are_recorded(
    platform: BackgroundServer, tmp_path: Path
) -> None:
    client = FakePlatformClient(platform.url)
    seed(client, tmp_path)
    runner = ScriptedRunner({2: ["Yes, Tuesday works."], 4: ["Please don't call me again."]})
    result, client = await execute(platform, runner)
    assert result["operatorDecision"] == "approved"
    summary = result["summary"]
    assert (summary["planned"], summary["skipped"], summary["called"], summary["answered"]) == (
        3,
        1,
        3,
        2,
    )
    assert (summary["completed"], summary["noAnswer"], summary["dnc"]) == (1, 1, 1)
    dispositions = {r["row"]: r["disposition"] for r in result["rows"]}
    assert dispositions == {2: "completed", 3: "no_answer", 4: "dnc"}
    results = sheet(client, "Results")
    assert results[0][:5] == ["source_row", "name", "phone_masked", "call_id", "disposition"]
    assert results[1][:5][:3] == ["2", "Asha Rao", "••••0101"] and results[1][4] == "completed"
    assert results[3][4] == "dnc"
    assert "+15555550101" not in str(results)
    # Agents write only within the configured resultRange, so the Contacts tab is untouched; the
    # owner is asked to mark the do-not-call row instead.
    contacts = sheet(client, "Contacts")
    assert [contacts[i][3] for i in (1, 2, 3)] == ["", "", ""]
    warnings = [
        e["payload"]
        for events in client.state("events").values()
        for e in events
        if e["type"] == "run.log" and e["payload"]["level"] == "warning"
    ]
    assert [w["fields"]["row"] for w in warnings if "dnc" in w["message"]] == [4]
    assert runner.prepared and runner.calls == [2, 3, 4]


async def test_unanswered_call_is_recorded_and_the_run_continues(
    platform: BackgroundServer, tmp_path: Path
) -> None:
    client = FakePlatformClient(platform.url)
    seed(client, tmp_path)
    result, _ = await execute(platform, ScriptedRunner({2: ["Sure."], 4: ["Sure."]}))
    assert [r["row"] for r in result["rows"]] == [2, 3, 4]
    assert result["rows"][1]["disposition"] == "no_answer"
    assert result["rows"][2]["disposition"] == "completed"


async def test_model_failure_fails_only_that_call(
    platform: BackgroundServer, tmp_path: Path
) -> None:
    client = FakePlatformClient(platform.url)
    seed(client, tmp_path)
    runner = ScriptedRunner({2: ["Hello?"], 4: ["Yes."]}, errors={2: "MODEL_ERROR"})
    result, _ = await execute(platform, runner)
    rows = {r["row"]: r for r in result["rows"]}
    assert rows[2]["disposition"] == "failed" and "MODEL_ERROR" in rows[2]["notes"]
    assert rows[4]["disposition"] == "completed"


async def test_rows_the_owner_marks_dnc_or_called_are_skipped_next_run(
    platform: BackgroundServer, tmp_path: Path
) -> None:
    client = FakePlatformClient(platform.url)
    seed(client, tmp_path)
    await execute(platform, ScriptedRunner({2: ["Yes."], 4: ["Stop calling me."]}))
    # The owner acts on the run's warning and results: row 4 asked not to be called, row 2 was
    # reached. A second run only reaches the contact who never answered.
    contacts = [list(r) for r in sheet(client, "Contacts")]
    contacts[1][3], contacts[3][3] = "called", "dnc"
    seed(client, tmp_path, rows=contacts)
    runner = ScriptedRunner({})
    result, _ = await execute(platform, runner)
    assert runner.calls == [3]
    assert result["summary"]["skipped"] == 3


async def test_retried_run_never_redials_a_row(platform: BackgroundServer, tmp_path: Path) -> None:
    client = FakePlatformClient(platform.url)
    seed(client, tmp_path)

    def placed_by_an_earlier_attempt(run_id: str, token: str) -> None:
        headers = {"Authorization": f"Bearer {token}"}
        sdk = f"{platform.url}/internal/v1/sdk"
        handshake = {"protocol": "v1alpha1", "sdkVersion": "t", "agentId": "voice-call-center"}
        httpx.post(f"{sdk}/handshake", json=handshake, headers=headers).raise_for_status()
        body = {"to": "+15555550101", "idempotencyKey": f"voice:{run_id}:2"}
        call = (
            httpx.post(f"{sdk}/voice/calls", json=body, headers=headers).raise_for_status().json()
        )
        httpx.post(f"{sdk}/actions/voice-call%3A2/claim", headers=headers).raise_for_status()
        httpx.post(
            f"{sdk}/actions/voice-call%3A2/complete", json={"result": call}, headers=headers
        ).raise_for_status()

    runner = ScriptedRunner({4: ["Yes."]})
    result, client = await execute(platform, runner, placed_by_an_earlier_attempt)
    assert runner.calls == [3, 4]  # row 2 is not rejoined
    rows = {r["row"]: r for r in result["rows"]}
    assert rows[2]["disposition"] == "failed" and "earlier attempt" in rows[2]["notes"]
    assert len(client.state("voice")) == 3  # row 2 was dialed once, by the "earlier attempt"


async def test_in_doubt_dial_is_not_repeated(platform: BackgroundServer, tmp_path: Path) -> None:
    client = FakePlatformClient(platform.url)
    seed(client, tmp_path)

    def claimed_but_never_completed(run_id: str, token: str) -> None:
        headers = {"Authorization": f"Bearer {token}"}
        sdk = f"{platform.url}/internal/v1/sdk"
        handshake = {"protocol": "v1alpha1", "sdkVersion": "t", "agentId": "voice-call-center"}
        httpx.post(f"{sdk}/handshake", json=handshake, headers=headers).raise_for_status()
        httpx.post(f"{sdk}/actions/voice-call%3A2/claim", headers=headers).raise_for_status()

    runner = ScriptedRunner({4: ["Yes."]})
    result, client = await execute(platform, runner, claimed_but_never_completed)
    rows = {r["row"]: r for r in result["rows"]}
    assert rows[2]["disposition"] == "failed" and rows[2]["callId"] is None
    assert "may have happened" in rows[2]["notes"]
    assert len(client.state("voice")) == 2


async def test_operator_cancel_places_no_calls(platform: BackgroundServer, tmp_path: Path) -> None:
    client = FakePlatformClient(platform.url)
    seed(client, tmp_path, answer="cancel")
    runner = ScriptedRunner({})
    result, client = await execute(platform, runner)
    assert result["operatorDecision"] == "cancelled"
    assert runner.calls == [] and client.state("voice") == []
    assert not runner.prepared


async def test_result_matches_the_manifest_schema(
    platform: BackgroundServer, tmp_path: Path
) -> None:
    from jsonschema import Draft202012Validator

    client = FakePlatformClient(platform.url)
    seed(client, tmp_path)
    result, _ = await execute(platform, ScriptedRunner({2: ["Yes."], 4: ["Yes."]}))
    schema = load_manifest(AGENT_DIR / "manifest.yaml")["spec"]["resultSchema"]
    Draft202012Validator(schema).validate(result)


async def test_voicemail_leaves_the_contact_eligible(
    platform: BackgroundServer, tmp_path: Path
) -> None:
    """A machine answering is not a person answering: the contact can be tried again later."""
    from voice_caller import session as session_module

    client = FakePlatformClient(platform.url)
    seed(client, tmp_path)

    class VoicemailRunner(ScriptedRunner):
        async def run_call(
            self, ctx: RunContext[Any], call: Any, contact: ContactRow, config: VoiceCallerConfig
        ) -> CallReport:
            report = await super().run_call(ctx, call, contact, config)
            if report.answered and contact.row == 2:
                report.ended_by = "voicemail_reached"
            return report

    assert session_module.CallReport is CallReport
    result, client = await execute(platform, VoicemailRunner({4: ["Yes."]}))
    assert {r["row"]: r["disposition"] for r in result["rows"]}[2] == "voicemail"
    assert sheet(client, "Contacts")[1][3] == ""
