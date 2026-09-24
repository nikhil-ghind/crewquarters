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

    async def request_action(self, model_id: str, action: str) -> dict[str, Any]: ...


class ConnectionStatusClient(Protocol):
    async def list_connections(self) -> list[dict[str, Any]]: ...


_FAKE_MODELS: list[dict[str, Any]] = [
    {
        "id": "local.general.small",
        "displayName": "General (small)",
        "family": "local.general",
        "downloadState": "INSTALLED",
        "memoryState": "NOT_LOADED",
        "diskBytes": 8_589_934_592,
        "expectedMemoryBytes": 17_179_869_184,
        "contextLimit": 8192,
        "capabilities": ["chat", "tools", "structured_output"],
        "validation": "fake",
        "license": {"name": "Apache-2.0", "gated": False},
        "activeLeases": [],
    },
    {
        "id": "local.general.quality",
        "displayName": "General (quality)",
        "family": "local.general",
        "downloadState": "NOT_INSTALLED",
        "memoryState": "NOT_LOADED",
        "diskBytes": 42_949_672_960,
        "expectedMemoryBytes": 64_424_509_440,
        "contextLimit": 8192,
        "capabilities": ["chat", "tools", "structured_output"],
        "validation": "fake",
        "license": {"name": "Apache-2.0", "gated": False},
        "activeLeases": [],
    },
    {
        "id": "local.embedding.small",
        "displayName": "Embedding (small, CPU)",
        "family": "local.embedding",
        "downloadState": "INSTALLED",
        "memoryState": "NOT_LOADED",
        "diskBytes": 134_217_728,
        "expectedMemoryBytes": 536_870_912,
        "contextLimit": 512,
        "capabilities": ["embedding"],
        "validation": "fake",
        "license": {"name": "Apache-2.0", "gated": False},
        "activeLeases": [],
    },
]

_ACTIONS = {
    "install": ("downloadState", {"NOT_INSTALLED", "DOWNLOAD_ERROR"}, "INSTALLED"),
    "load": ("memoryState", {"NOT_LOADED", "LOAD_ERROR"}, "READY"),
    "unload": ("memoryState", {"READY"}, "NOT_LOADED"),
}


class FakeModelStatusClient:
    def __init__(self) -> None:
        self._models = {m["id"]: copy.deepcopy(m) for m in _FAKE_MODELS}

    async def list_models(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(m) for m in self._models.values()]

    async def get_model(self, model_id: str) -> dict[str, Any] | None:
        model = self._models.get(model_id)
        return copy.deepcopy(model) if model else None

    async def request_action(self, model_id: str, action: str) -> dict[str, Any]:
        model = self._models.get(model_id)
        if model is None:
            raise not_found("Model", model_id)
        field, allowed, target = _ACTIONS[action]
        if action == "load" and model["downloadState"] != "INSTALLED":
            raise conflict("MODEL_NOT_INSTALLED", "Install the model before loading it.")
        if model[field] not in allowed:
            raise conflict("INVALID_MODEL_STATE", f"Cannot {action} while {model[field]}.")
        model[field] = target
        return copy.deepcopy(model)


_PROVIDERS = {
    "google": {"displayName": "Google", "capabilities": ["gmail.readonly", "spreadsheets"]},
    "twilio": {"displayName": "Twilio", "capabilities": ["call.fixed_script"]},
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
