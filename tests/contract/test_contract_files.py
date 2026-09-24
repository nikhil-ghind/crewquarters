"""Every draft contract parses, is a valid schema/OpenAPI document, and carries its draft markers."""

from typing import Any

import pytest
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate

from crewquarters_contracts import loader

DOCUMENTS = {
    "manifest": loader.manifest_schema,
    "capabilities": loader.capabilities,
    "broker": loader.broker_openapi,
    "control": loader.control_openapi,
    "events": loader.run_event_schema,
}


def markers(doc: dict[str, Any]) -> dict[str, Any]:
    info = doc.get("info", {})
    return {k: doc.get(k, info.get(k)) for k in ("x-status", "x-owner", "x-drafted-by")}


@pytest.mark.parametrize("name", sorted(DOCUMENTS))
def test_document_has_draft_markers(name: str) -> None:
    found = markers(DOCUMENTS[name]())
    assert found["x-status"] == "draft"
    assert found["x-drafted-by"] == "Person 5"
    assert found["x-owner"] in {"Person 1", "Person 3"}


def test_manifest_schema_is_valid_2020_12() -> None:
    Draft202012Validator.check_schema(loader.manifest_schema())


def test_run_event_schema_is_valid_2020_12() -> None:
    Draft202012Validator.check_schema(loader.run_event_schema())


@pytest.mark.parametrize("name", ["broker", "control"])
def test_openapi_documents_are_valid(name: str) -> None:
    validate(DOCUMENTS[name]())


def test_every_capability_has_description_and_ui_copy() -> None:
    caps = loader.capabilities()["capabilities"]
    assert set(caps) == {
        "input.ask",
        "llm.local",
        "llm.cloud.openai",
        "llm.cloud.anthropic",
        "knowledge.search",
        "google.gmail.readonly",
        "google.sheets",
        "twilio.voice.call",
    }
    for name, entry in caps.items():
        assert entry["description"], name
        assert entry["uiCopy"], name


def test_broker_operations_declare_capability_and_idempotency() -> None:
    doc = loader.broker_openapi()
    for path, item in doc["paths"].items():
        for method, op in item.items():
            if method not in {"get", "post"}:
                continue
            assert "x-capability" in op, f"{method} {path}"
            assert isinstance(op.get("x-idempotent"), (bool, str)), f"{method} {path}"
            assert op.get("operationId"), f"{method} {path}"
