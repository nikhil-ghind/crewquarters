"""The fake's control API follows the canonical /api/v1 semantics (contracts/openapi.yaml)."""

import copy

import httpx
from fake_helpers import NO_PERMISSIONS, create_run, install, manifest

API = "/api/v1"
UNBUILT = "localhost:5001/crewquarters/probe@sha256:REQUIRED_DIGEST"


async def test_import_rejects_unbuilt_manifest(api: httpx.AsyncClient) -> None:
    m = manifest()
    m["spec"]["image"] = UNBUILT
    r = await api.post(f"{API}/catalog/agents/import", json={"manifest": m})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_MANIFEST"


async def test_import_rejects_manifest_missing_a_permission_entry(api: httpx.AsyncClient) -> None:
    m = manifest()
    del m["spec"]["permissions"]["cloudProviders"]
    r = await api.post(f"{API}/catalog/agents/import", json={"manifest": m})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_MANIFEST"


async def test_import_is_idempotent_and_versions_are_immutable(api: httpx.AsyncClient) -> None:
    m = manifest()
    first = await api.post(f"{API}/catalog/agents/import", json={"manifest": m})
    again = await api.post(f"{API}/catalog/agents/import", json={"manifest": m})
    assert first.status_code == again.status_code == 201
    assert first.json()["latest"]["imageDigest"] == "sha256:" + "b" * 64
    assert first.json()["trustStatus"] == "imported_unreviewed"
    m["spec"]["image"] = "localhost:5001/crewquarters/probe@sha256:" + "c" * 64
    conflict = await api.post(f"{API}/catalog/agents/import", json={"manifest": m})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "AGENT_VERSION_IMMUTABLE"


async def test_admin_catalog_accepts_unbuilt_manifest(api: httpx.AsyncClient) -> None:
    m = manifest()
    m["spec"]["image"] = UNBUILT
    r = await api.post("/fake/v1/catalog", json={"manifest": m})
    assert r.status_code == 200
    # The canonical AgentVersionOut requires a digest; unbuilt dev manifests report a placeholder.
    assert r.json()["latest"]["imageDigest"] == "sha256:" + "0" * 64
    assert r.json()["latest"]["image"] == UNBUILT
    listing = await api.get(f"{API}/catalog/agents")
    assert [e["agentId"] for e in listing.json()["items"]] == ["probe"]


async def test_install_applies_defaults_and_binds_profiles(api: httpx.AsyncClient) -> None:
    await install(api)
    [body] = (await api.get("/fake/v1/state/installations")).json()
    assert body["config"] == {"timezone": "UTC", "limit": 5}
    assert body["modelBindings"] == {"local.general": "local.general.small"}
    assert body["capabilities"] == [
        "events.write",
        "idempotency",
        "llm.profile:local.general.small",
        "user_input",
    ]


async def test_install_rejects_invalid_config(api: httpx.AsyncClient) -> None:
    m = manifest()
    await api.post(f"{API}/catalog/agents/import", json={"manifest": m})
    r = await api.post(
        f"{API}/agent-installations",
        json={
            "agentId": "probe",
            "config": {"limit": 0},
            "approvedPermissions": m["spec"]["permissions"],
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_CONFIGURATION"


async def test_install_rejects_model_profile_outside_the_bindings(api: httpx.AsyncClient) -> None:
    m = manifest()
    await api.post(f"{API}/catalog/agents/import", json={"manifest": m})
    r = await api.post(
        f"{API}/agent-installations",
        json={
            "agentId": "probe",
            "config": {"timezone": "UTC", "modelProfile": "local.embedding.small"},
            "approvedPermissions": m["spec"]["permissions"],
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MODEL_PROFILE_NOT_PERMITTED"


async def test_install_requires_exactly_the_requested_permissions(api: httpx.AsyncClient) -> None:
    m = manifest()
    await api.post(f"{API}/catalog/agents/import", json={"manifest": m})
    more = copy.deepcopy(m["spec"]["permissions"])
    more["connectors"]["google"] = ["gmail.readonly"]
    fewer = {**copy.deepcopy(NO_PERMISSIONS), "userInput": True}
    for approved in (more, fewer):
        r = await api.post(
            f"{API}/agent-installations",
            json={
                "agentId": "probe",
                "config": {"timezone": "UTC"},
                "approvedPermissions": approved,
            },
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "PERMISSIONS_NOT_APPROVED"


async def test_owner_can_choose_the_variant_of_a_family(api: httpx.AsyncClient) -> None:
    m = manifest()
    await api.post(f"{API}/catalog/agents/import", json={"manifest": m})
    r = await api.post(
        f"{API}/agent-installations",
        json={
            "agentId": "probe",
            "config": {"timezone": "UTC"},
            "approvedPermissions": m["spec"]["permissions"],
            "modelBindings": {"local.general": "local.general.quality"},
        },
    )
    assert r.status_code == 201, r.text
    assert "llm.profile:local.general.quality" in r.json()["capabilities"]


async def test_manual_run_starts_queued_and_is_listed(api: httpx.AsyncClient) -> None:
    installation = await install(api)
    run = (await api.post(f"{API}/runs", json={"installationId": installation})).json()
    assert run["state"] == "QUEUED"
    assert run["trigger"] == "manual"
    assert run["currentAttempt"] == 1
    listing = await api.get(f"{API}/runs")
    assert [r["id"] for r in listing.json()["items"]] == [run["id"]]


async def test_scheduled_runs_come_from_the_admin_api(api: httpx.AsyncClient) -> None:
    installation = await install(api)
    r = await api.post(
        "/fake/v1/runs", json={"installationId": installation, "trigger": "schedule"}
    )
    assert r.status_code == 422
    run_id = await create_run(
        api, installation, trigger="schedule", scheduledFor="2026-09-24T04:30:00Z"
    )
    run = (await api.get(f"{API}/runs/{run_id}")).json()
    assert (run["trigger"], run["scheduledFor"]) == ("schedule", "2026-09-24T04:30:00Z")


async def test_cancel_queued_run_is_immediate(api: httpx.AsyncClient) -> None:
    installation = await install(api)
    run_id = await create_run(api, installation)
    r = await api.post(f"{API}/runs/{run_id}/cancel")
    assert r.json()["state"] == "CANCELLED"
    # Cancelling again is a no-op, as in the control plane.
    again = await api.post(f"{API}/runs/{run_id}/cancel")
    assert (again.status_code, again.json()["state"]) == (200, "CANCELLED")


async def test_finished_run_cannot_be_cancelled(api: httpx.AsyncClient) -> None:
    installation = await install(api)
    run_id = await create_run(api, installation)
    await api.post(f"/fake/v1/runs/{run_id}/dispatch", json={"brokerUrl": "x"})
    await api.post(f"/fake/v1/runs/{run_id}/exited", json={"attempt": 1, "exitCode": 1})
    r = await api.post(f"{API}/runs/{run_id}/cancel")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "RUN_NOT_CANCELLABLE"


async def test_retry_is_rejected_for_queued_run(api: httpx.AsyncClient) -> None:
    installation = await install(api)
    run_id = await create_run(api, installation)
    r = await api.post(f"{API}/runs/{run_id}/retry")
    assert r.status_code == 409


async def test_unknown_run_is_404_with_envelope(api: httpx.AsyncClient) -> None:
    r = await api.get(f"{API}/runs/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
