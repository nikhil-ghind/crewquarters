"""Locate and parse the contract files.

In a built wheel the files are packaged under ``crewquarters_contracts/data``. In an editable
(workspace) install they are read from ``packages/contracts`` in the repository. Returned documents
are cached and shared: callers must not mutate them.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

import yaml

_PACKAGE_DATA = Path(__file__).parent / "data"
_REPOSITORY = Path(__file__).resolve().parents[2]


def contracts_dir() -> Path:
    if (_PACKAGE_DATA / "agent-manifest.schema.json").is_file():
        return _PACKAGE_DATA
    return _REPOSITORY


@cache
def _load(relative: str) -> dict[str, Any]:
    path = contracts_dir() / relative
    text = path.read_text(encoding="utf-8")
    document = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    if not isinstance(document, dict):
        raise ValueError(f"{path} does not contain a mapping")
    return document


def manifest_schema() -> dict[str, Any]:
    return _load("agent-manifest.schema.json")


def capabilities() -> dict[str, Any]:
    return _load("capabilities.yaml")


def broker_openapi() -> dict[str, Any]:
    return _load("broker-sdk.openapi.yaml")


def control_openapi() -> dict[str, Any]:
    return _load("openapi.yaml")


def run_event_schema() -> dict[str, Any]:
    return _load("events/run-event.schema.json")
