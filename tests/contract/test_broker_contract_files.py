"""The broker SDK contract (Person 3's, drafted by Person 5; stable for v1alpha1) is a valid
OpenAPI document that uses the canonical capability vocabulary and mirrors the control plane's
``/internal/v1`` run API, and every bundled agent manifest passes the control plane's manifest
validation."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from openapi_spec_validator import validate

from crewquarters_fake import contracts

REPO = Path(__file__).resolve().parents[2]
MANIFESTS = sorted(
    [*REPO.glob("agents/*/manifest.yaml"), *REPO.glob("tests/integration/agents/*/manifest.yaml")]
)
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def operations(document: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, op)
        for path, item in document["paths"].items()
        for method, op in item.items()
        if method in HTTP_METHODS
    ]


def test_broker_contract_is_stable_and_names_its_owner() -> None:
    info = contracts.broker_openapi()["info"]
    assert (info["version"], info["x-status"], info["x-owner"]) == (
        "v1alpha1",
        "stable",
        "Person 3",
    )
    assert "DRAFT" not in info["description"]


def test_broker_draft_is_valid_openapi() -> None:
    validate(contracts.broker_openapi())


def test_broker_operations_declare_capability_and_idempotency() -> None:
    for method, path, op in operations(contracts.broker_openapi()):
        assert "x-capability" in op, f"{method} {path}"
        assert isinstance(op.get("x-idempotent"), (bool, str)), f"{method} {path}"
        assert op.get("operationId"), f"{method} {path}"


def test_broker_capabilities_come_from_the_canonical_vocabulary() -> None:
    vocabulary = yaml.safe_load((contracts.contracts_dir() / "capabilities.yaml").read_text())
    patterns = {entry["pattern"] for entry in vocabulary["capabilities"]}
    used = {
        str(op["x-capability"]).split(" ")[0]
        for _, _, op in operations(contracts.broker_openapi())
        if op["x-capability"] is not None
    }
    assert used <= patterns, used - patterns


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_bundled_manifests_pass_control_plane_validation(path: Path) -> None:
    normalized, issues = contracts.validate_manifest(
        contracts.load_manifest(path), allow_unbuilt=True
    )
    assert issues == []
    assert normalized is not None


@pytest.mark.parametrize(
    "agent", ["contract_probe", "gmail_digest", "caller"], ids=lambda a: str(a)
)
def test_agent_result_schemas_name_a_renderer(agent: str) -> None:
    manifest = contracts.load_manifest(REPO / "agents" / agent / "manifest.yaml")
    renderer = manifest["spec"]["resultSchema"]["x-crewquarters-renderer"]
    assert renderer.startswith("crewquarters.") and renderer.endswith("/v1")


# Broker draft schema -> the control plane's /internal/v1 schema it passes through to. Request
# bodies differ only by `attempt`, which the broker adds from the verified run token.
MIRRORED_REQUESTS = {
    "ResultRequest": "RunResultIn",
    "InputRequestCreate": "AskIn",
    "ActionComplete": "ActionCompleteIn",
}
MIRRORED_RESPONSES = {
    "RunStateResponse": "HeartbeatOut",
    "ActionRecord": "ActionOut",
}


def schemas() -> tuple[dict[str, Any], dict[str, Any]]:
    broker = contracts.broker_openapi()["components"]["schemas"]
    control = contracts.control_openapi()["components"]["schemas"]
    return broker, control


@pytest.mark.parametrize(("draft", "canonical"), sorted(MIRRORED_REQUESTS.items()))
def test_broker_requests_mirror_the_internal_api(draft: str, canonical: str) -> None:
    broker, control = schemas()
    assert set(broker[draft]["properties"]) == set(control[canonical]["properties"]) - {"attempt"}
    assert set(broker[draft].get("required", [])) == set(control[canonical]["required"]) - {
        "attempt"
    }


@pytest.mark.parametrize(("draft", "canonical"), sorted(MIRRORED_RESPONSES.items()))
def test_broker_responses_mirror_the_internal_api(draft: str, canonical: str) -> None:
    broker, control = schemas()
    assert set(broker[draft]["properties"]) == set(control[canonical]["properties"])
    assert set(broker[draft]["required"]) == set(control[canonical]["required"])


def test_broker_input_request_is_a_view_of_input_request_out() -> None:
    broker, control = schemas()
    view, full = broker["InputRequest"], control["InputRequestOut"]
    assert set(view["properties"]) <= set(full["properties"])
    assert set(view["required"]) <= set(full["required"])
    assert view.get("additionalProperties", True) is True  # the full object also validates


def test_agent_event_types_match_the_internal_api() -> None:
    broker, control = schemas()
    assert (
        broker["AgentEvent"]["properties"]["type"]["enum"]
        == (control["AgentEventIn"]["properties"]["type"]["enum"])
    )
