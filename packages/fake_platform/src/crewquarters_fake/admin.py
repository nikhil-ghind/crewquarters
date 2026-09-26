"""Test-only admin API (/fake/v1): seeding, dispatch, fault injection, and state inspection."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from crewquarters_fake import scenario, services
from crewquarters_fake.control import store_of
from crewquarters_fake.errors import ApiError
from crewquarters_fake.store import AutoAnswer
from crewquarters_fake.views import (
    action_view,
    catalog_view,
    event_view,
    input_control_view,
    installation_view,
    run_view,
    voice_call_view,
)

router = APIRouter(prefix="/fake/v1")


class ManifestIn(BaseModel):
    manifest: dict[str, Any]


class DispatchIn(BaseModel):
    brokerUrl: str


class ExitIn(BaseModel):
    attempt: int
    exitCode: int


class FaultIn(BaseModel):
    target: str
    mode: Literal["error", "delay", "apply-then-drop"]
    count: int = 1
    status: int | None = None
    code: str | None = None
    delayMs: int = 0


class AutoAnswerIn(BaseModel):
    keyPattern: str
    value: Any = None
    delaySeconds: float = 0.0


class ScenarioIn(BaseModel):
    path: str
    now: str | None = None


class ScheduledRunIn(BaseModel):
    installationId: str
    trigger: Literal["manual", "schedule"] = "schedule"
    scheduledFor: str | None = None


class TranscriptsIn(BaseModel):
    lines: list[str]
    channel: str = ""  # a voice call id, or "" when no call is in progress


class ConnectionIn(BaseModel):
    provider: Literal["google", "twilio"]
    status: Literal["connected", "expired", "missing"]


@router.post("/reset")
async def reset(request: Request) -> dict[str, Any]:
    store_of(request).reset()
    return {"reset": True}


@router.post("/scenarios/load")
async def load_scenario(body: ScenarioIn, request: Request) -> dict[str, Any]:
    store = store_of(request)
    path = Path(body.path)
    if not path.is_absolute() and store.settings.scenarios_dir is not None:
        path = store.settings.scenarios_dir / path
    if not (path / "scenario.yaml").is_file():
        raise ApiError(404, "NOT_FOUND", f"no scenario.yaml in {path}")
    now = datetime.fromisoformat(body.now) if body.now else None
    return scenario.load(store, path, now=now)


@router.post("/catalog")
async def register(body: ManifestIn, request: Request) -> dict[str, Any]:
    store = store_of(request)
    return catalog_view(store, services.import_manifest(store, body.manifest, allow_unbuilt=True))


@router.post("/runs", status_code=201)
async def create_scheduled_run(body: ScheduledRunIn, request: Request) -> dict[str, Any]:
    """Create a run the way the scheduler does (the public API only starts manual runs)."""
    store = store_of(request)
    run = services.create_run(store, body.installationId, body.trigger, body.scheduledFor, None)
    return run_view(store, run)


@router.post("/runs/{run_id}/dispatch")
async def dispatch(run_id: str, body: DispatchIn, request: Request) -> dict[str, Any]:
    store = store_of(request)
    launch = services.dispatch(store, run_id, body.brokerUrl)
    await store.notify()
    return launch


@router.post("/runs/{run_id}/exited")
async def exited(run_id: str, body: ExitIn, request: Request) -> dict[str, Any]:
    store = store_of(request)
    run = services.report_exit(store, run_id, body.attempt, body.exitCode)
    await store.notify()
    return run_view(store, run)


@router.post("/faults")
async def add_fault(body: FaultIn, request: Request) -> dict[str, Any]:
    faults = store_of(request).faults
    faults.add(body.target, body.mode, body.count, body.status, body.code, body.delayMs)
    return {"faults": len(store_of(request).faults.list())}


@router.delete("/faults")
async def clear_faults(request: Request) -> dict[str, Any]:
    store_of(request).faults.clear()
    return {"faults": 0}


@router.post("/auto-answers")
async def add_auto_answer(body: AutoAnswerIn, request: Request) -> dict[str, Any]:
    store_of(request).auto_answers.append(
        AutoAnswer(body.keyPattern, body.value, body.delaySeconds)
    )
    return {"autoAnswers": len(store_of(request).auto_answers)}


@router.post("/connections")
async def set_connection(body: ConnectionIn, request: Request) -> dict[str, Any]:
    store_of(request).connections[body.provider] = body.status
    return dict(store_of(request).connections)


@router.post("/speech/transcripts")
async def queue_transcripts(body: TranscriptsIn, request: Request) -> dict[str, Any]:
    """Queue what the fake speech-to-text will "hear" next (in order)."""
    store_of(request).fake_speech.queue_transcripts(body.lines, body.channel)
    return {"queued": len(body.lines)}


@router.get("/state/{kind}")
async def state(kind: str, request: Request) -> Any:
    store = store_of(request)
    if kind == "installations":
        return [installation_view(store, i) for i in store.installations.values()]
    if kind == "runs":
        return [run_view(store, r) for r in store.runs.values()]
    if kind == "events":
        return {run_id: [event_view(e) for e in items] for run_id, items in store.events.items()}
    if kind == "inputs":
        return [input_control_view(store, r) for r in store.inputs.values()]
    if kind == "actions":
        return [{"runId": run_id, **action_view(r)} for (run_id, _), r in store.actions.items()]
    if kind == "audit":
        return store.audit
    if kind == "traffic":
        return store.traffic
    if kind == "calls":
        return store.twilio.snapshot()
    if kind == "sheets":
        return store.sheets.snapshot()
    if kind == "llm":
        return store.gateway.log
    if kind == "voice":
        return [voice_call_view(c, None) for c in store.voice_calls.values()]
    if kind == "speech":
        return {
            "synthesized": store.fake_speech.synthesized,
            "transcribed": store.fake_speech.transcribed,
        }
    if kind == "gmail":
        return sorted(store.gmail.messages)
    if kind == "connections":
        return store.connections
    raise ApiError(404, "NOT_FOUND", f"unknown state kind {kind}")
