"""Shared setup for fake-platform router tests."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

import httpx

DIGEST = "sha256:" + "b" * 64
SDK = "/internal/v1/sdk"

BASE_MANIFEST: dict[str, Any] = {
    "apiVersion": "crewquarters/v1alpha1",
    "kind": "Agent",
    "metadata": {"id": "probe", "name": "Probe", "version": "0.1.0"},
    "spec": {
        "image": f"localhost:5001/crewquarters/probe@{DIGEST}",
        "entrypoint": ["python", "-m", "probe"],
        "architectures": ["linux/amd64", "linux/arm64"],
        "triggers": ["manual", "schedule"],
        "permissions": {"userInput": True, "llmProfiles": ["local.general"]},
        "resources": {"cpu": 1, "memoryMb": 256, "activeTimeoutSeconds": 600, "maxInputWaitSeconds": 3600},
        "configurationSchema": {
            "type": "object",
            "required": ["timezone"],
            "properties": {
                "timezone": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "default": 5},
                "modelProfile": {"type": "string", "x-crewquarters-widget": "modelProfile"},
            },
        },
    },
}


def manifest(**permissions: Any) -> dict[str, Any]:
    m = copy.deepcopy(BASE_MANIFEST)
    if permissions:
        m["spec"]["permissions"] = permissions
    return m


@dataclass
class Started:
    run_id: str
    token: str
    installation_id: str
    headers: dict[str, str]


async def install(api: httpx.AsyncClient, m: dict[str, Any] | None = None, **config: Any) -> str:
    m = m or manifest()
    r = await api.post("/api/v1/catalog/agents:import", json={"manifest": m})
    assert r.status_code == 200, r.text
    body = {
        "agentId": m["metadata"]["id"],
        "version": m["metadata"]["version"],
        "config": {"timezone": "UTC", **config},
    }
    r = await api.post("/api/v1/agent-installations", json=body)
    assert r.status_code == 200, r.text
    return str(r.json()["id"])


async def start(
    api: httpx.AsyncClient, m: dict[str, Any] | None = None, *, handshake: bool = True, **run: Any
) -> Started:
    installation_id = await install(api, m)
    r = await api.post("/api/v1/runs", json={"installationId": installation_id, **run})
    assert r.status_code == 200, r.text
    run_id = r.json()["id"]
    r = await api.post(f"/fake/v1/runs/{run_id}/dispatch", json={"brokerUrl": "http://fake"})
    assert r.status_code == 200, r.text
    token = r.json()["env"]["PLATFORM_RUN_TOKEN"]
    headers = {"Authorization": f"Bearer {token}"}
    if handshake:
        r = await api.post(
            f"{SDK}/handshake",
            json={"protocol": "v1alpha1", "sdkVersion": "t", "agentId": "probe"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
    return Started(run_id, token, installation_id, headers)


async def run_state(api: httpx.AsyncClient, run_id: str) -> str:
    r = await api.get(f"/api/v1/runs/{run_id}")
    return str(r.json()["state"])


async def events(api: httpx.AsyncClient, run_id: str) -> list[dict[str, Any]]:
    r = await api.get(f"/api/v1/runs/{run_id}/events")
    return list(r.json()["items"])
