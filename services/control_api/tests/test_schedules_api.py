from __future__ import annotations

import httpx
import pytest

from conftest import install_hello

pytestmark = pytest.mark.usefixtures("catalog_synced")


async def test_schedule_crud_and_preview(owner: httpx.AsyncClient) -> None:
    installation = await install_hello(owner)
    preview = await owner.post(
        "/api/v1/schedules/preview", json={"cron": "0 10 * * *", "timezone": "Asia/Kolkata"}
    )
    assert preview.status_code == 200
    occurrences = preview.json()["occurrences"]
    assert len(occurrences) == 3
    assert all(o["local"].endswith("+05:30") and "T10:00:00" in o["local"] for o in occurrences)

    created = await owner.post(
        "/api/v1/schedules",
        json={
            "installationId": installation["id"],
            "cron": "0 10 * * *",
            "timezone": "Asia/Kolkata",
        },
    )
    assert created.status_code == 201, created.text
    schedule = created.json()
    assert schedule["misfirePolicy"] == "fire_once" and schedule["agentName"] == "Hello Crew"
    assert schedule["nextRunAt"] == schedule["nextOccurrences"][0]["at"]

    disabled = await owner.patch(
        f"/api/v1/schedules/{schedule['id']}",
        json={"version": schedule["version"], "enabled": False},
    )
    assert disabled.json()["nextRunAt"] is None and disabled.json()["nextOccurrences"] == []
    moved = await owner.patch(
        f"/api/v1/schedules/{schedule['id']}",
        json={"version": disabled.json()["version"], "enabled": True, "timezone": "Europe/London"},
    )
    assert moved.json()["nextOccurrences"][0]["zoneAbbreviation"] in {"BST", "GMT"}

    deleted = await owner.delete(f"/api/v1/schedules/{schedule['id']}")
    assert deleted.status_code == 204
    assert (await owner.get(f"/api/v1/schedules/{schedule['id']}")).status_code == 404


@pytest.mark.parametrize(
    ("cron", "timezone", "code"),
    [
        ("0 10 * * *", "IST", "INVALID_TIMEZONE"),
        ("0 10 * * *", "EST", "INVALID_TIMEZONE"),
        ("0 10 * *", "UTC", "INVALID_CRON"),
        ("61 10 * * *", "UTC", "INVALID_CRON"),
    ],
)
async def test_schedule_validation(
    owner: httpx.AsyncClient, cron: str, timezone: str, code: str
) -> None:
    installation = await install_hello(owner)
    response = await owner.post(
        "/api/v1/schedules",
        json={"installationId": installation["id"], "cron": cron, "timezone": timezone},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
