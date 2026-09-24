"""Contract checks: OpenAPI drift, migrations from empty, and schema conformance."""

from __future__ import annotations

import json

import httpx
import pytest
import yaml
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from jsonschema import Draft202012Validator
from sqlalchemy import create_engine, inspect, text

from conftest import ROOT, alembic_config, fresh_database, install_hello
from crewquarters_shared import capability
from crewquarters_shared.db.base import Base

CONTRACTS = ROOT / "packages/contracts"


@pytest.mark.no_db
def test_openapi_matches_committed_contract() -> None:
    from crewquarters_api.openapi import render_openapi

    committed = (CONTRACTS / "openapi.yaml").read_text()
    assert render_openapi() == committed, "Run `make contracts` and commit the result."


@pytest.mark.no_db
def test_migrations_upgrade_downgrade_from_empty_database() -> None:
    with fresh_database() as url:
        cfg = alembic_config(url)
        command.upgrade(cfg, "head")
        engine = create_engine(url)
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names())
            assert {"users", "agent_runs", "jobs", "schedules", "audit_events"} <= tables
            assert (
                conn.execute(
                    text("SELECT count(*) FROM pg_extension WHERE extname='vector'")
                ).scalar()
                == 1
            )
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
            assert diff == [], f"models and migrations differ: {diff}"
        command.downgrade(cfg, "base")
        with engine.connect() as conn:
            assert set(inspect(conn).get_table_names()) == {"alembic_version"}
        command.upgrade(cfg, "head")
        engine.dispose()


@pytest.mark.no_db
def test_bundled_manifests_and_schemas_are_valid() -> None:
    manifest_schema = json.loads((CONTRACTS / "agent-manifest.schema.json").read_text())
    Draft202012Validator.check_schema(manifest_schema)
    event_schema = json.loads((CONTRACTS / "events/run-event.schema.json").read_text())
    Draft202012Validator.check_schema(event_schema)
    for path in (ROOT / "catalog").rglob("*.yaml"):
        errors = list(
            Draft202012Validator(manifest_schema).iter_errors(yaml.safe_load(path.read_text()))
        )
        assert errors == [], f"{path}: {errors[0].message}"


@pytest.mark.no_db
def test_capability_vocabulary_covers_derived_capabilities() -> None:
    vocab = yaml.safe_load((CONTRACTS / "capabilities.yaml").read_text())
    prefixes = {c["pattern"].split("<")[0] for c in vocab["capabilities"]}
    caps = capability.capabilities_from_permissions(
        {
            "llmProfiles": ["local.general"],
            "knowledge": ["config"],
            "connectors": {
                "google": ["gmail.readonly", "spreadsheets"],
                "twilio": ["call.fixed_script"],
            },
            "cloudProviders": ["openai", "anthropic"],
            "userInput": True,
        }
    )
    for cap in caps:
        assert any(cap == p or (p.endswith(":") and cap.startswith(p)) for p in prefixes), cap


@pytest.mark.usefixtures("catalog_synced")
async def test_emitted_run_events_conform_to_event_schema(
    owner: httpx.AsyncClient, platform
) -> None:
    from conftest import wait_for_state

    validator = Draft202012Validator(
        json.loads((CONTRACTS / "events/run-event.schema.json").read_text()),
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    for scenario in ("ask", "fail", "model"):
        installation = await install_hello(owner, {"fakeScenario": scenario})
        run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
        if scenario == "ask":
            await wait_for_state(owner, run["id"], {"WAITING_INPUT"})
            request = (await owner.get("/api/v1/input-requests")).json()["items"][0]
            await owner.post(
                f"/api/v1/input-requests/{request['id']}/answer",
                json={"version": request["version"], "value": {"decision": "continue"}},
            )
        await wait_for_state(owner, run["id"], {"SUCCEEDED", "FAILED"})
        events = (await owner.get(f"/api/v1/runs/{run['id']}/events/history")).json()
        assert events
        for event in events:
            errors = list(validator.iter_errors(event))
            assert errors == [], f"{event['type']}: {errors[0].message}"
