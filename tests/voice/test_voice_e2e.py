"""The voice agent end to end (marker `voice`): the agent process holds real WebRTC calls through a
local LiveKit server with a simulated callee on the other end.

Speech is the fake engine by default: the callee's lines are "heard" exactly, and the agent's
voice is a real speech clip, so voice-activity and end-of-turn detection run on real audio.
Set CREWQ_VOICE_SPEECH_URL to a running speech server (make voice-up) to use the real models
(marker `models`).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from crewquarters_fake.app import create_app
from crewquarters_fake.client import FakePlatformClient
from crewquarters_fake.contracts import load_manifest
from crewquarters_fake.harness import RunOutcome, run_agent
from crewquarters_fake.launcher import ProcessLauncher
from crewquarters_fake.server import BackgroundServer
from crewquarters_fake.settings import FakeSettings

pytestmark = pytest.mark.voice
REPO = Path(__file__).resolve().parents[2]
AGENT = REPO / "agents" / "voice_caller"
SPEECH_URL = os.environ.get("CREWQ_VOICE_SPEECH_URL") or None
FULL_NUMBER = re.compile(r"\+1555555010\d")
SCRIPT = [
    "Hello?",
    "Yes, I have a minute.",
    "Tuesday at three still works for me.",
    "No questions, thanks. Bye.",
]
CONFIG = {
    "spreadsheetId": "voice-sheet",
    "organization": "Acme Dental",
    "purpose": "confirm next week's cleaning appointments",
    "questions": ["Does your appointment time still work?", "Any questions for the dentist?"],
    "maxCallSeconds": 120,
    "ringTimeoutSeconds": 15,
}
RULES = [
    {
        "name": "outcome",
        "match": {"schemaTitle": "CallOutcome"},
        "respond": {
            "json": {
                "disposition": "completed",
                "interest": "high",
                "callback_requested": False,
                "follow_up": "",
                "notes": "Confirmed Tuesday at three.",
            }
        },
    },
    {
        "name": "agreed",
        "match": {"lastUser": True, "contains": ["minute"]},
        "respond": {"text": "Great. Does your cleaning next Tuesday at three still work for you?"},
    },
    {
        "name": "confirmed",
        "match": {"lastUser": True, "contains": ["works"]},
        "respond": {"text": "Perfect, you're all set. Any questions for the dentist?"},
    },
    {
        "name": "goodbye",
        "match": {"lastUser": True, "contains": ["bye"]},
        "respond": {
            "text": "Thanks, have a great day.",
            "toolCall": {"name": "end_call", "arguments": {}},
        },
    },
    {
        "name": "fallback",
        "match": {"lastUser": True},
        "respond": {"text": "Sorry, could you say that again?"},
    },
]


@pytest.fixture
def platform(livekit_url: str) -> Iterator[tuple[BackgroundServer, Any]]:
    settings = FakeSettings(
        heartbeat_seconds=0.5,
        livekit_url=livekit_url,
        speech_url=SPEECH_URL,
        record_traffic=True,
    )
    app = create_app(settings)
    with BackgroundServer(app) as server:
        yield server, app.state.store


def scenario_dir(tmp: Path) -> Path:
    doc = {
        "name": "voice-e2e",
        "timezone": "UTC",
        "inputs": {
            "autoAnswers": [
                {"keyPattern": "confirm-voice-calls-v1:*", "value": {"choice": "approve"}}
            ]
        },
        "sheets": {
            "spreadsheets": {
                "voice-sheet": {
                    "Contacts": [
                        ["name", "phone_e164", "consent", "status"],
                        ["Asha Rao", "+15555550101", "yes", ""],
                        ["Ben Ortiz", "+15555550102", "yes", ""],
                        ["Chen Li", "+15555550103", "yes", ""],
                    ],
                    "Results": [],
                }
            }
        },
        "voice": {
            "callees": {
                "+15555550101": {"outcome": "answer", "ringSeconds": 1, "script": SCRIPT},
                "+15555550102": {"outcome": "voicemail", "ringSeconds": 1},
                "+15555550103": {"outcome": "busy", "ringSeconds": 1},
            }
        },
        "llm": {"rules": RULES},
    }
    (tmp / "scenario.yaml").write_text(yaml.safe_dump(doc))
    return tmp


def run_voice_agent(server: BackgroundServer, tmp: Path) -> RunOutcome:
    client = FakePlatformClient(server.url)
    client.load_scenario(str(scenario_dir(tmp)))
    manifest = load_manifest(AGENT / "manifest.yaml")
    client.register_manifest(manifest)
    installation = client.install(
        "voice-call-center", "0.1.0", CONFIG, manifest["spec"]["permissions"]
    )
    launcher = ProcessLauncher(manifest["spec"]["entrypoint"], agent_dir=AGENT, log_dir=tmp)
    return run_agent(client, launcher, installation["id"], timeout=300)


def test_a_real_call_loop_through_local_livekit(platform: Any, tmp_path: Path) -> None:
    server, store = platform
    outcome = run_voice_agent(server, tmp_path)
    assert outcome.state == "SUCCEEDED", outcome.log[-4000:]
    rows = {r["row"]: r for r in outcome.result["rows"]}
    assert rows[2]["disposition"] == "completed", outcome.log[-4000:]
    assert rows[3]["disposition"] == "voicemail", outcome.log[-4000:]
    assert rows[4]["disposition"] == "busy"
    assert rows[2]["durationSeconds"] and rows[2]["durationSeconds"] > 0

    if SPEECH_URL is None:
        # Every line the callee said after the greeting was heard, in order, from real audio
        # segmented by the agent's VAD. (The pickup "Hello?" can come before the agent is
        # listening; the agent then says "Hello?" itself.)
        heard = [t for t in store.fake_speech.transcribed if t in SCRIPT[1:]]
        assert heard == SCRIPT[1:], store.fake_speech.transcribed
        spoken = store.fake_speech.synthesized
        greeting = next(s for s in spoken if "Acme Dental" in s)
        assert "automated AI assistant" in greeting.split(".")[0]
        assert spoken[0] == "Hello?" or spoken[0] == greeting

    everything = json.dumps(outcome.events) + outcome.log + json.dumps(outcome.result)
    assert not FULL_NUMBER.search(everything), "a full phone number leaked"
    results = store.sheets.snapshot()["voice-sheet"]
    assert results["Contacts"][1][3] == "called"
    assert results["Contacts"][2][3] == "" and results["Contacts"][3][3] == ""
    metrics = [e for e in outcome.events if e["type"] == "run.metric"]
    assert any(m["payload"]["name"] == "turn_latency_p50_ms" for m in metrics)

    # Every JSON request and response the agent exchanged matches the broker draft, including
    # voice calls and the OpenAI-compatible model facade.
    from jsonschema import Draft202012Validator
    from openapi_check import find_operation, request_schema, response_schema, validator

    from crewquarters_fake import contracts

    broker = contracts.broker_openapi()
    problems, seen = [], set()
    for record in store.traffic:
        if not record["path"].startswith("/internal/v1/sdk"):
            continue
        if record["path"] == "/internal/v1/sdk/openai/v1/":
            # The TTS plugin's connection warm-up (GET on the base URL); its response is ignored.
            continue
        operation = find_operation(broker, "/internal/v1/sdk", record["method"], record["path"])
        assert operation is not None, record["path"]
        seen.add(operation["operationId"])
        for schema, body in (
            (request_schema(operation), record["requestBody"]),
            (response_schema(broker, operation, record["status"]), record["responseBody"]),
        ):
            if schema is not None and body is not None:
                problems += [e.message for e in validator(broker, schema).iter_errors(body)]
    assert problems == []
    assert {
        "createVoiceCall",
        "getVoiceCall",
        "hangupVoiceCall",
        "openaiChatCompletions",
        "openaiTranscriptions",
        "openaiSpeech",
    } <= seen
    event_validator = Draft202012Validator(
        contracts.run_event_schema(), format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    assert [e.message for ev in outcome.events for e in event_validator.iter_errors(ev)] == []
