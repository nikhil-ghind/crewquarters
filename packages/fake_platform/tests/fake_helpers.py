"""Shared setup for fake-platform router tests (canonical v1alpha1 manifests and routes)."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

import httpx

DIGEST = "sha256:" + "b" * 64
SDK = "/internal/v1/sdk"
NO_PERMISSIONS: dict[str, Any] = {
    "llmProfiles": [],
    "knowledge": [],
    "connectors": {"google": [], "twilio": []},
    "cloudProviders": [],
    "userInput": False,
}

BASE_MANIFEST: dict[str, Any] = {
    "apiVersion": "crewquarters/v1alpha1",
    "kind": "Agent",
    "metadata": {"id": "probe", "name": "Probe", "version": "0.1.0"},
    "spec": {
        "image": f"localhost:5001/crewquarters/probe@{DIGEST}",
        "entrypoint": ["python", "-m", "probe"],
        "architectures": ["linux/amd64", "linux/arm64"],
        "triggers": ["manual", "schedule"],
        "permissions": {
            **copy.deepcopy(NO_PERMISSIONS),
            "userInput": True,
            "llmProfiles": ["local.general"],
        },
        "resources": {
            "cpu": 1,
            "memoryMb": 256,
            "activeTimeoutSeconds": 600,
            "maxInputWaitSeconds": 3600,
        },
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
    """The base manifest; keyword arguments replace individual permission entries."""
    m = copy.deepcopy(BASE_MANIFEST)
    if permissions:
        m["spec"]["permissions"] = {**copy.deepcopy(NO_PERMISSIONS), **permissions}
    return m


@dataclass
class Started:
    run_id: str
    token: str
    installation_id: str
    headers: dict[str, str]


async def install(api: httpx.AsyncClient, m: dict[str, Any] | None = None, **config: Any) -> str:
    """Import the manifest and install it, approving exactly the requested permissions."""
    m = m or manifest()
    r = await api.post("/api/v1/catalog/agents/import", json={"manifest": m})
    assert r.status_code in (200, 201), r.text
    body = {
        "agentId": m["metadata"]["id"],
        "version": m["metadata"]["version"],
        "config": {"timezone": "UTC", **config},
        "approvedPermissions": m["spec"]["permissions"],
    }
    r = await api.post("/api/v1/agent-installations", json=body)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def create_run(api: httpx.AsyncClient, installation_id: str, **run: Any) -> str:
    """A manual run through the control API, or a scheduled one through the admin API."""
    if run:
        r = await api.post("/fake/v1/runs", json={"installationId": installation_id, **run})
    else:
        r = await api.post("/api/v1/runs", json={"installationId": installation_id})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def start(
    api: httpx.AsyncClient, m: dict[str, Any] | None = None, *, handshake: bool = True, **run: Any
) -> Started:
    installation_id = await install(api, m)
    run_id = await create_run(api, installation_id, **run)
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
    r = await api.get(f"/api/v1/runs/{run_id}/events/history")
    return list(r.json())


async def audit(api: httpx.AsyncClient) -> list[dict[str, Any]]:
    r = await api.get("/fake/v1/state/audit")
    return list(r.json())
