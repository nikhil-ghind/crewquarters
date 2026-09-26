"""Clients the control API uses to read state owned by other services.

* Model status belongs to the model gateway (Akshay Sunil Navani, Person 2).
* Connection status belongs to the capability broker (Nikhil Sajan Khaneja, Person 3).

Each has a fake used by the ``dev`` profile and tests. The HTTP implementations are
added by the owning service once its internal API is frozen.
"""

from __future__ import annotations

import copy
from typing import Any, Protocol

from crewquarters_shared.errors import conflict, not_found


class ModelStatusClient(Protocol):
    async def list_models(self) -> list[dict[str, Any]]: ...

    async def get_model(self, model_id: str) -> dict[str, Any] | None: ...

    async def request_action(
        self, model_id: str, action: str, *, force: bool = False, actor: str | None = None
    ) -> dict[str, Any]: ...

    async def memory(self) -> dict[str, Any]: ...


class ConnectionStatusClient(Protocol):
    async def list_connections(self) -> list[dict[str, Any]]: ...


def _fake_model(ident: str, name: str, installed: bool, peak: int) -> dict[str, Any]:
    """Same shape as the model gateway's GET /internal/v1/models/{id}."""
    return {
        "id": ident,
        "displayName": name,
        "family": ".".join(ident.split(".")[:2]),
        "backend": "mock",
        "downloadState": "INSTALLED" if installed else "NOT_INSTALLED",
        "memoryState": "NOT_LOADED",
        "stage": None,
        "diskBytes": 5908 if installed else None,
        "download": {"bytesDone": 0, "bytesTotal": None, "currentFile": None, "revision": None},
        "expectedMemoryBytes": peak,
        "reservedBytes": 0,
        "contextLimit": 8192,
        "capabilities": ["chat", "structured_output", "streaming"],
        "validation": "mock",
        "license": {"name": "Apache-2.0", "gated": False, "url": None},
        "error": None,
        "loadStartedAt": None,
        "readyAt": None,
        "idleUnloadAt": None,
        "activeLeases": [],
    }


_FAKE_MODELS: list[dict[str, Any]] = [
    _fake_model("local.general.small", "General (small) - mock", True, 2 * 1024**3),
    _fake_model("local.general.quality", "General (quality) - mock", False, 6 * 1024**3),
]

_ACTIONS = {
    "install": ("downloadState", {"NOT_INSTALLED", "DOWNLOAD_ERROR"}, "INSTALLED"),
    "cancel_install": ("downloadState", {"DOWNLOADING", "DOWNLOAD_ERROR"}, "NOT_INSTALLED"),
    "delete": ("downloadState", {"INSTALLED"}, "NOT_INSTALLED"),
    "load": ("memoryState", {"NOT_LOADED", "LOAD_ERROR", "ERROR"}, "READY"),
    "unload": ("memoryState", {"READY"}, "NOT_LOADED"),
}


class FakeModelStatusClient:
    """In-memory stand-in for the model gateway (control API unit tests only)."""

    def __init__(self) -> None:
        self._models = {m["id"]: copy.deepcopy(m) for m in _FAKE_MODELS}

    async def list_models(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(m) for m in self._models.values()]

    async def get_model(self, model_id: str) -> dict[str, Any] | None:
        model = self._models.get(model_id)
        return copy.deepcopy(model) if model else None

    async def request_action(
        self, model_id: str, action: str, *, force: bool = False, actor: str | None = None
    ) -> dict[str, Any]:
        model = self._models.get(model_id)
        if model is None:
            raise not_found("Model", model_id)
        field, allowed, target = _ACTIONS[action]
        if action == "load" and model["downloadState"] != "INSTALLED":
            raise conflict("MODEL_NOT_INSTALLED", "Install the model before loading it.")
        if action == "delete" and model["memoryState"] == "READY":
            raise conflict("MODEL_RESIDENT", "Unload the model before deleting its files.")
        if model[field] not in allowed:
            raise conflict("INVALID_MODEL_STATE", f"Cannot {action} while {model[field]}.")
        model[field] = target
        return copy.deepcopy(model)

    async def memory(self) -> dict[str, Any]:
        return {
            "totalBytes": 128 * 1024**3,
            "availableBytes": 100 * 1024**3,
            "systemReserveBytes": 24 * 1024**3,
            "maxServingBytes": 96 * 1024**3,
            "safetyMarginBytes": 8 * 1024**3,
            "reservedBytes": 0,
            "models": [],
        }


_PROVIDERS = {
    "google": {"displayName": "Google", "capabilities": ["gmail.readonly", "spreadsheets"]},
    "twilio": {"displayName": "Twilio", "capabilities": ["call.fixed_script"]},
    "github": {
        "displayName": "GitHub",
        "capabilities": ["pull_requests.read", "pull_requests.write"],
    },
    "openai": {"displayName": "OpenAI", "capabilities": ["cloud.openai"]},
    "anthropic": {"displayName": "Anthropic", "capabilities": ["cloud.anthropic"]},
}


class FakeConnectionStatusClient:
    def __init__(self, connected: list[str]) -> None:
        self._connected = set(connected)

    async def list_connections(self) -> list[dict[str, Any]]:
        return [
            {
                "provider": provider,
                "displayName": info["displayName"],
                "status": "CONNECTED" if provider in self._connected else "NOT_CONNECTED",
                "grantedCapabilities": info["capabilities"] if provider in self._connected else [],
                "lastCheckedAt": None,
            }
            for provider, info in _PROVIDERS.items()
        ]
