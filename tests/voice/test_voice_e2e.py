"""The voice agent end to end (marker `voice`): real WebRTC calls through a local LiveKit server,
with a simulated callee on the other end.

- As a local process against an in-process fake platform (`make voice-e2e`). Speech is the fake
  engine by default: the callee's lines are "heard" exactly, and the agent's voice is a real speech
  clip, so voice-activity and end-of-turn detection run on real audio. Set CREWQ_VOICE_SPEECH_URL
  to a running speech server (`make voice-up`) to use the real models.
- In its hardened container against the Compose fake platform, with LiveKit in containers mode
  (marker `e2e`, `make voice-e2e-containers`): the deployment shape on the appliance.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from crewquarters_fake.app import create_app
from crewquarters_fake.client import FakePlatformClient
from crewquarters_fake.contracts import load_manifest
from crewquarters_fake.harness import RunOutcome, run_agent
from crewquarters_fake.launcher import DockerLauncher, ProcessLauncher
from crewquarters_fake.server import BackgroundServer
from crewquarters_fake.settings import FakeSettings

pytestmark = pytest.mark.voice
REPO = Path(__file__).resolve().parents[2]
AGENT = REPO / "agents" / "voice_caller"
SCENARIO = "tests/fixtures/scenarios/voice-e2e"
PINNED = REPO / ".e2e" / "manifests" / "voice_caller.yaml"
PLATFORM_URL = os.environ.get("CREWQ_E2E_PLATFORM_URL", "http://127.0.0.1:8090")
SPEECH_URL = os.environ.get("CREWQ_VOICE_SPEECH_URL") or None
FULL_NUMBER = re.compile(r"\+1555555010\d")
SCRIPT: list[str] = yaml.safe_load((REPO / SCENARIO / "scenario.yaml").read_text())["voice"][
    "callees"
]["+15555550101"]["script"]
CONFIG = {
    "spreadsheetId": "voice-sheet",
    "organization": "Acme Dental",
    "purpose": "confirm next week's cleaning appointments",
    "questions": ["Does your appointment time still work?", "Any questions for the dentist?"],
    "maxCallSeconds": 120,
    "ringTimeoutSeconds": 15,
}


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


def run_voice_agent(
    client: FakePlatformClient,
    manifest: dict[str, Any],
    launcher: ProcessLauncher | DockerLauncher,
    scenario: str,
) -> RunOutcome:
    client.load_scenario(scenario)
    if isinstance(launcher, DockerLauncher):
        client.import_manifest(manifest)  # pinned by digest, as the control plane requires
    else:
        client.register_manifest(manifest)  # a local checkout: no image yet
    installation = client.install(
        "voice-call-center",
        manifest["metadata"]["version"],
        CONFIG,
        manifest["spec"]["permissions"],
    )
    return run_agent(client, launcher, installation["id"], timeout=300)


def check_calls(outcome: RunOutcome, sheets: dict[str, Any]) -> None:
    """Dispositions, the sheet, masking, and metrics: the same wherever the agent runs."""
    assert outcome.state == "SUCCEEDED", outcome.log[-4000:]
    rows = {r["row"]: r for r in outcome.result["rows"]}
    assert rows[2]["disposition"] == "completed", outcome.log[-4000:]
    assert rows[3]["disposition"] == "voicemail", outcome.log[-4000:]
    assert rows[4]["disposition"] == "busy"
    assert rows[2]["durationSeconds"] and rows[2]["durationSeconds"] > 0
    everything = json.dumps(outcome.events) + outcome.log + json.dumps(outcome.result)
    assert not FULL_NUMBER.search(everything), "a full phone number leaked"
    contacts = sheets["voice-sheet"]["Contacts"]
    assert contacts[1][3] == "called"
    assert contacts[2][3] == "" and contacts[3][3] == ""
    metrics = [e for e in outcome.events if e["type"] == "run.metric"]
    assert any(m["payload"]["name"] == "turn_latency_p50_ms" for m in metrics)


def check_fake_speech(transcribed: list[str], synthesized: list[str]) -> None:
    """Every line the callee said after the greeting was heard, in order, from real audio segmented
    by the agent's VAD. (The pickup "Hello?" can come before the agent is listening; the agent then
    says "Hello?" itself.) The greeting discloses the automated call in its first sentence."""
    heard = [t for t in transcribed if t in SCRIPT[1:]]
    assert heard == SCRIPT[1:], transcribed
    greeting = next(s for s in synthesized if "Acme Dental" in s)
    assert "automated AI assistant" in greeting.split(".")[0]
    assert synthesized[0] == "Hello?" or synthesized[0] == greeting


def test_a_real_call_loop_through_local_livekit(platform: Any, tmp_path: Path) -> None:
    server, store = platform
    manifest = load_manifest(AGENT / "manifest.yaml")
    launcher = ProcessLauncher(manifest["spec"]["entrypoint"], agent_dir=AGENT, log_dir=tmp_path)
    outcome = run_voice_agent(
        FakePlatformClient(server.url), manifest, launcher, str(REPO / SCENARIO)
    )
    check_calls(outcome, store.sheets.snapshot())
    if SPEECH_URL is None:
        check_fake_speech(store.fake_speech.transcribed, store.fake_speech.synthesized)

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


@pytest.mark.e2e
def test_the_hardened_agent_container_holds_the_calls(livekit_url: str, tmp_path: Path) -> None:
    if os.environ.get("CREWQ_VOICE_LIVEKIT_MODE") != "containers":
        pytest.skip("needs LiveKit in containers mode; use `make voice-e2e-containers`")
    try:
        httpx.get(f"{PLATFORM_URL}/health/ready", timeout=3).raise_for_status()
    except httpx.HTTPError:
        pytest.skip("the fake platform is not running; use `make voice-e2e-containers`")
    if not PINNED.is_file():
        pytest.skip("the voice agent image is not pinned; use `make voice-e2e-containers`")
    client = FakePlatformClient(PLATFORM_URL)
    client.reset()
    try:
        outcome = run_voice_agent(
            client, load_manifest(PINNED), DockerLauncher(log_dir=tmp_path), SCENARIO
        )
        check_calls(outcome, client.state("sheets"))
        speech = client.state("speech")
        check_fake_speech(speech["transcribed"], speech["synthesized"])
    finally:
        client.close()
