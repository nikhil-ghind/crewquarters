from __future__ import annotations

import copy

import httpx
import pytest
import yaml
from sqlalchemy import text

from conftest import HELLO_PERMISSIONS, ROOT, install_hello

pytestmark = pytest.mark.usefixtures("catalog_synced")


def hello_manifest() -> dict:
    return yaml.safe_load((ROOT / "catalog/dev/hello-crew.yaml").read_text())


async def test_catalog_lists_compatible_bundled_agent(owner: httpx.AsyncClient) -> None:
    agents = (await owner.get("/api/v1/catalog/agents")).json()["items"]
    assert [a["agentId"] for a in agents] == ["hello-crew"]
    hello = agents[0]
    assert hello["trustStatus"] == "curated" and hello["installed"] is False
    assert hello["latest"]["compatible"] is True
    assert hello["latest"]["imageDigest"].startswith("sha256:")


async def test_install_requires_exact_permission_approval(owner: httpx.AsyncClient) -> None:
    fewer = copy.deepcopy(HELLO_PERMISSIONS)
    fewer["userInput"] = False
    response = await owner.post(
        "/api/v1/agent-installations",
        json={"agentId": "hello-crew", "config": {}, "approvedPermissions": fewer},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PERMISSIONS_NOT_APPROVED"


async def test_install_resolves_profile_family_and_validates_config(
    owner: httpx.AsyncClient,
) -> None:
    installation = await install_hello(owner)
    assert installation["modelBindings"] == {"local.general": "local.general.small"}
    assert installation["config"]["fakeScenario"] == "succeed"  # schema default applied
    assert "llm.profile:local.general.small" in installation["capabilities"]
    assert installation["readiness"]["ready"] is True

    bad = await owner.post(
        "/api/v1/agent-installations",
        json={
            "agentId": "hello-crew",
            "config": {"fakeScenario": "explode"},
            "approvedPermissions": HELLO_PERMISSIONS,
        },
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_CONFIGURATION"

    not_permitted = await owner.post(
        "/api/v1/agent-installations",
        json={
            "agentId": "hello-crew",
            "config": {"modelProfile": "local.general.quality"},
            "approvedPermissions": HELLO_PERMISSIONS,
        },
    )
    assert not_permitted.status_code == 422
    assert not_permitted.json()["error"]["code"] == "MODEL_PROFILE_NOT_PERMITTED"


async def test_owner_can_choose_variant_and_readiness_reports_missing_model(
    owner: httpx.AsyncClient,
) -> None:
    response = await owner.post(
        "/api/v1/agent-installations",
        json={
            "agentId": "hello-crew",
            "config": {"modelProfile": "local.general.quality"},
            "approvedPermissions": HELLO_PERMISSIONS,
            "modelBindings": {"local.general": "local.general.quality"},
        },
    )
    assert response.status_code == 201, response.text
    readiness = response.json()["readiness"]
    assert readiness["ready"] is False
    model_check = next(c for c in readiness["checks"] if c["name"] == "model")
    assert model_check["status"] == "missing" and model_check["resource"] == "local.general.quality"

    run = await owner.post("/api/v1/runs", json={"installationId": response.json()["id"]})
    assert run.status_code == 409 and run.json()["error"]["code"] == "INSTALLATION_NOT_READY"


async def test_import_cannot_shadow_bundled_agent(owner: httpx.AsyncClient) -> None:
    manifest = hello_manifest()
    manifest["metadata"]["version"] = "0.2.0"
    imported = await owner.post("/api/v1/catalog/agents/import", json={"manifest": manifest})
    assert imported.status_code == 409
    assert imported.json()["error"]["code"] == "CATALOG_SOURCE_CONFLICT"


async def test_permission_change_blocks_runs_until_reapproved(
    owner: httpx.AsyncClient, sessions
) -> None:
    from crewquarters_api.catalog import upsert_manifest

    installation = await install_hello(owner)
    manifest = hello_manifest()
    manifest["metadata"]["version"] = "0.2.0"
    manifest["spec"]["permissions"]["connectors"] = {"google": ["gmail.readonly"]}
    async with sessions() as db:
        await upsert_manifest(db, manifest, source="bundled")
        await db.commit()

    upgraded = await owner.patch(
        f"/api/v1/agent-installations/{installation['id']}",
        json={"version": installation["version"], "agentVersion": "0.2.0"},
    )
    assert upgraded.status_code == 200, upgraded.text
    body = upgraded.json()
    assert body["needsReapproval"] is True and body["readiness"]["ready"] is False
    blocked = await owner.post("/api/v1/runs", json={"installationId": installation["id"]})
    assert blocked.status_code == 409

    approved = await owner.patch(
        f"/api/v1/agent-installations/{installation['id']}",
        json={"version": body["version"], "approvedPermissions": manifest["spec"]["permissions"]},
    )
    assert approved.status_code == 200
    assert approved.json()["needsReapproval"] is False
    assert "google.gmail.readonly" in approved.json()["capabilities"]
    started = await owner.post("/api/v1/runs", json={"installationId": installation["id"]})
    assert started.status_code == 201

    stale = await owner.patch(
        f"/api/v1/agent-installations/{installation['id']}", json={"version": 1, "enabled": False}
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"


async def test_manifest_validation_rejects_mutable_image(owner: httpx.AsyncClient) -> None:
    manifest = hello_manifest()
    manifest["metadata"]["id"] = "dev-imported"
    manifest["spec"]["image"] = "ghcr.io/crewquarters/hello-crew:latest"
    response = await owner.post("/api/v1/catalog/agents/import", json={"manifest": manifest})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_MANIFEST"
    assert error["details"]["errors"][0]["path"] == "/spec/image"


async def test_import_and_version_immutability(owner: httpx.AsyncClient, sessions) -> None:
    manifest = hello_manifest()
    manifest["metadata"]["id"] = "dev-imported"
    first = await owner.post("/api/v1/catalog/agents/import", json={"manifest": manifest})
    assert first.status_code == 201
    assert first.json()["trustStatus"] == "imported_unreviewed"
    same = await owner.post("/api/v1/catalog/agents/import", json={"manifest": manifest})
    assert same.status_code == 201
    manifest["spec"]["resources"]["cpu"] = 1
    changed = await owner.post("/api/v1/catalog/agents/import", json={"manifest": manifest})
    assert (
        changed.status_code == 409 and changed.json()["error"]["code"] == "AGENT_VERSION_IMMUTABLE"
    )
    async with sessions() as db:
        with pytest.raises(Exception, match="immutable"):
            await db.execute(text("UPDATE agent_versions SET version = '9.9.9'"))


async def test_uninstall_blocked_by_active_run_and_removes_schedules(
    owner: httpx.AsyncClient,
) -> None:
    installation = await install_hello(owner)
    schedule = await owner.post(
        "/api/v1/schedules",
        json={
            "installationId": installation["id"],
            "cron": "0 10 * * *",
            "timezone": "Asia/Kolkata",
        },
    )
    assert schedule.status_code == 201
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    blocked = await owner.delete(f"/api/v1/agent-installations/{installation['id']}")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "INSTALLATION_IN_USE"

    await owner.post(f"/api/v1/runs/{run['id']}/cancel")
    deleted = await owner.delete(f"/api/v1/agent-installations/{installation['id']}")
    assert deleted.status_code == 204
    assert (await owner.get(f"/api/v1/agent-installations/{installation['id']}")).status_code == 404
    assert (await owner.get("/api/v1/schedules")).json()["items"] == []
