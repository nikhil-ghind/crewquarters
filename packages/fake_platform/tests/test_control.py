import httpx
from helpers import manifest

API = "/api/v1"


async def test_import_rejects_unbuilt_manifest(api: httpx.AsyncClient) -> None:
    m = manifest()
    m["spec"]["image"] = "localhost:5001/crewquarters/probe@sha256:REQUIRED_DIGEST"
    r = await api.post(f"{API}/catalog/agents:import", json={"manifest": m})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MANIFEST_INVALID"


async def test_import_is_idempotent_and_conflicts_on_new_digest(api: httpx.AsyncClient) -> None:
    m = manifest()
    first = await api.post(f"{API}/catalog/agents:import", json={"manifest": m})
    again = await api.post(f"{API}/catalog/agents:import", json={"manifest": m})
    assert first.status_code == again.status_code == 200
    assert first.json()["imageDigest"] == "sha256:" + "b" * 64
    assert first.json()["trustStatus"] == "local-import"
    m["spec"]["image"] = "localhost:5001/crewquarters/probe@sha256:" + "c" * 64
    conflict = await api.post(f"{API}/catalog/agents:import", json={"manifest": m})
    assert conflict.status_code == 409


async def test_admin_catalog_accepts_unbuilt_manifest(api: httpx.AsyncClient) -> None:
    m = manifest()
    m["spec"]["image"] = "localhost:5001/crewquarters/probe@sha256:REQUIRED_DIGEST"
    r = await api.post("/fake/v1/catalog", json={"manifest": m})
    assert r.status_code == 200
    assert r.json()["trustStatus"] == "unbuilt-dev"
    listing = await api.get(f"{API}/catalog/agents")
    assert [e["agentId"] for e in listing.json()["items"]] == ["probe"]


async def test_install_applies_defaults_and_resolves_profiles(api: httpx.AsyncClient) -> None:
    await api.post(f"{API}/catalog/agents:import", json={"manifest": manifest()})
    r = await api.post(
        f"{API}/agent-installations",
        json={"agentId": "probe", "version": "0.1.0", "config": {"timezone": "UTC"}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["config"] == {"timezone": "UTC", "limit": 5}
    assert body["resolvedProfiles"] == ["local.general.small"]
    assert body["capabilities"] == ["input.ask", "llm.local"]


async def test_install_rejects_invalid_config(api: httpx.AsyncClient) -> None:
    await api.post(f"{API}/catalog/agents:import", json={"manifest": manifest()})
    r = await api.post(
        f"{API}/agent-installations", json={"agentId": "probe", "version": "0.1.0", "config": {"limit": 0}}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "CONFIG_INVALID"


async def test_install_rejects_model_profile_outside_approved_family(api: httpx.AsyncClient) -> None:
    await api.post(f"{API}/catalog/agents:import", json={"manifest": manifest()})
    r = await api.post(
        f"{API}/agent-installations",
        json={
            "agentId": "probe",
            "version": "0.1.0",
            "config": {"timezone": "UTC", "modelProfile": "local.embedding.small"},
        },
    )
    assert r.status_code == 422


async def test_install_rejects_permissions_the_manifest_did_not_request(api: httpx.AsyncClient) -> None:
    await api.post(f"{API}/catalog/agents:import", json={"manifest": manifest()})
    r = await api.post(
        f"{API}/agent-installations",
        json={
            "agentId": "probe",
            "version": "0.1.0",
            "config": {"timezone": "UTC"},
            "approvedPermissions": {"userInput": True, "connectors": {"google": ["gmail.readonly"]}},
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "PERMISSION_NOT_REQUESTED"


async def test_install_can_approve_a_subset(api: httpx.AsyncClient) -> None:
    await api.post(f"{API}/catalog/agents:import", json={"manifest": manifest()})
    r = await api.post(
        f"{API}/agent-installations",
        json={
            "agentId": "probe",
            "version": "0.1.0",
            "config": {"timezone": "UTC"},
            "approvedPermissions": {"userInput": True},
        },
    )
    assert r.json()["capabilities"] == ["input.ask"]
    assert r.json()["resolvedProfiles"] == []


async def install(api: httpx.AsyncClient) -> str:
    await api.post(f"{API}/catalog/agents:import", json={"manifest": manifest()})
    r = await api.post(
        f"{API}/agent-installations",
        json={"agentId": "probe", "version": "0.1.0", "config": {"timezone": "UTC"}},
    )
    return str(r.json()["id"])


async def test_create_run_deduplicates_on_idempotency_key(api: httpx.AsyncClient) -> None:
    installation = await install(api)
    headers = {"Idempotency-Key": "manual-1"}
    a = await api.post(f"{API}/runs", json={"installationId": installation}, headers=headers)
    b = await api.post(f"{API}/runs", json={"installationId": installation}, headers=headers)
    assert a.json()["id"] == b.json()["id"]
    assert a.json()["state"] == "QUEUED"
    assert a.json()["currentAttempt"] == 0
    listing = await api.get(f"{API}/runs")
    assert len(listing.json()["items"]) == 1


async def test_schedule_trigger_requires_scheduled_for(api: httpx.AsyncClient) -> None:
    installation = await install(api)
    r = await api.post(f"{API}/runs", json={"installationId": installation, "trigger": "schedule"})
    assert r.status_code == 422
    r = await api.post(
        f"{API}/runs",
        json={"installationId": installation, "trigger": "schedule", "scheduledFor": "2026-09-24T04:30:00Z"},
    )
    assert r.json()["scheduledFor"] == "2026-09-24T04:30:00Z"


async def test_cancel_queued_run_is_immediate(api: httpx.AsyncClient) -> None:
    installation = await install(api)
    run = (await api.post(f"{API}/runs", json={"installationId": installation})).json()
    r = await api.post(f"{API}/runs/{run['id']}/cancel")
    assert r.json()["state"] == "CANCELLED"
    again = await api.post(f"{API}/runs/{run['id']}/cancel")
    assert again.status_code == 409


async def test_retry_is_rejected_for_queued_run(api: httpx.AsyncClient) -> None:
    installation = await install(api)
    run = (await api.post(f"{API}/runs", json={"installationId": installation})).json()
    r = await api.post(f"{API}/runs/{run['id']}/retry")
    assert r.status_code == 409


async def test_unknown_run_is_404_with_envelope(api: httpx.AsyncClient) -> None:
    r = await api.get(f"{API}/runs/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
