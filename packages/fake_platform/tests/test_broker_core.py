"""Broker lifecycle, input, and action routes. Semantics mirror the control plane's
/internal/v1 run API (crewquarters_shared.runs.service), which the broker passes through."""

import asyncio
from typing import Any

import httpx
from fake_helpers import SDK, audit, events, manifest, run_state, start

API = "/api/v1"
HANDSHAKE = {"protocol": "v1alpha1", "sdkVersion": "t", "agentId": "probe"}


def ask_body(key: str = "confirm-v1", timeout: int = 600, **extra: Any) -> dict[str, Any]:
    return {
        "key": key,
        "title": "Continue?",
        "prompt": "Should the agent continue?",
        "schema": {
            "type": "object",
            "required": ["choice"],
            "properties": {"choice": {"type": "string", "enum": ["yes", "no"]}},
        },
        "preview": {
            "blocks": [{"type": "text", "text": "Two calls"}],
            "choices": [{"value": "yes", "label": "Yes", "style": "primary"}],
        },
        "timeoutSeconds": timeout,
        **extra,
    }


def event(client_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "clientEventId": client_id,
        "type": event_type,
        "occurredAt": "2026-09-24T10:00:00Z",
        "payload": payload,
    }


async def test_handshake_moves_run_to_running_and_returns_context(api: httpx.AsyncClient) -> None:
    started = await start(api, handshake=False)
    assert await run_state(api, started.run_id) == "PREPARING"
    r = await api.post(f"{SDK}/handshake", json=HANDSHAKE, headers=started.headers)
    body = r.json()
    assert body["run"]["id"] == started.run_id
    assert body["run"]["attempt"] == 1
    assert body["config"] == {"timezone": "UTC", "limit": 5}
    assert body["capabilities"] == [
        "events.write",
        "idempotency",
        "llm.profile:local.general.small",
        "user_input",
    ]
    assert body["grants"]["llmProfiles"] == ["local.general.small"]
    assert body["grants"]["modelBindings"] == {"local.general": "local.general.small"}
    assert body["limits"] == {"activeTimeoutSeconds": 600, "inputWaitRemainingSeconds": 3600}
    assert body["heartbeatIntervalSeconds"] == 0.05
    assert await run_state(api, started.run_id) == "RUNNING"
    changes = [
        (e["payload"]["from"], e["payload"]["to"])
        for e in await events(api, started.run_id)
        if e["type"] == "run.state_changed"
    ]
    assert changes == [(None, "QUEUED"), ("QUEUED", "PREPARING"), ("PREPARING", "RUNNING")]


async def test_handshake_rejects_other_protocols(api: httpx.AsyncClient) -> None:
    started = await start(api, handshake=False)
    r = await api.post(
        f"{SDK}/handshake", json={**HANDSHAKE, "protocol": "v2"}, headers=started.headers
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "PROTOCOL_UNSUPPORTED"


async def test_unknown_token_is_unauthenticated(api: httpx.AsyncClient) -> None:
    r = await api.post(f"{SDK}/heartbeat", json={}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_events_are_sequenced_and_deduplicated(api: httpx.AsyncClient) -> None:
    started = await start(api)
    batch = {
        "events": [
            event("a", "run.log", {"level": "info", "message": "x"}),
            event("b", "run.progress", {"percent": 50, "message": "half"}),
        ]
    }
    first = await api.post(f"{SDK}/events", json=batch, headers=started.headers)
    again = await api.post(f"{SDK}/events", json=batch, headers=started.headers)
    assert first.json()["accepted"] == 2
    assert again.json()["accepted"] == 0
    history = await events(api, started.run_id)
    agent_events = [e for e in history if e["type"] in {"run.log", "run.progress"}]
    assert [e["payload"]["message"] for e in agent_events] == ["x", "half"]
    sequences = [e["sequence"] for e in history]
    assert sequences == list(range(1, len(sequences) + 1))


async def test_agents_cannot_emit_platform_event_types(api: httpx.AsyncClient) -> None:
    started = await start(api)
    batch = {"events": [event("a", "run.state_changed", {"from": None, "to": "RUNNING"})]}
    r = await api.post(f"{SDK}/events", json=batch, headers=started.headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_EVENT_TYPE"


async def test_events_must_match_the_run_event_schema(api: httpx.AsyncClient) -> None:
    started = await start(api)
    batch = {"events": [event("a", "run.log", {"level": "loud", "message": "x"})]}
    r = await api.post(f"{SDK}/events", json=batch, headers=started.headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_EVENT"


async def test_first_result_wins(api: httpx.AsyncClient) -> None:
    started = await start(api)
    body = {"status": "succeeded", "result": {"n": 1}}
    first = await api.post(f"{SDK}/result", json=body, headers=started.headers)
    assert first.json() == {"state": "SUCCEEDED", "cancelRequested": False}
    other = await api.post(
        f"{SDK}/result",
        json={"status": "failed", "error": {"code": "X", "message": "late"}},
        headers=started.headers,
    )
    assert other.json()["state"] == "SUCCEEDED"
    run = (await api.get(f"{API}/runs/{started.run_id}")).json()
    assert run["result"] == {"n": 1}
    results = [e["payload"] for e in await events(api, started.run_id) if e["type"] == "run.result"]
    assert results == [{"status": "succeeded"}]


async def test_failed_result_records_error_and_can_be_retried(api: httpx.AsyncClient) -> None:
    started = await start(api)
    error = {"code": "AGENT_ERROR", "message": "boom", "retryable": True}
    r = await api.post(
        f"{SDK}/result", json={"status": "failed", "error": error}, headers=started.headers
    )
    assert r.json()["state"] == "FAILED"
    run = (await api.get(f"{API}/runs/{started.run_id}")).json()
    assert run["error"]["code"] == "AGENT_ERROR"
    retried = (await api.post(f"{API}/runs/{started.run_id}/retry")).json()
    assert (retried["state"], retried["currentAttempt"]) == ("QUEUED", 2)


async def test_non_retryable_failure_cannot_be_retried(api: httpx.AsyncClient) -> None:
    started = await start(api)
    error = {"code": "CONFIG_INVALID", "message": "bad", "retryable": False}
    await api.post(
        f"{SDK}/result", json={"status": "failed", "error": error}, headers=started.headers
    )
    r = await api.post(f"{API}/runs/{started.run_id}/retry")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "RUN_NOT_RETRYABLE"


async def test_cancel_running_run_goes_through_cancelling(api: httpx.AsyncClient) -> None:
    started = await start(api)
    r = await api.post(f"{API}/runs/{started.run_id}/cancel")
    assert r.json()["state"] == "CANCELLING"
    heartbeat = await api.post(f"{SDK}/heartbeat", json={}, headers=started.headers)
    assert heartbeat.json() == {"state": "CANCELLING", "cancelRequested": True}
    denied = await api.post(f"{SDK}/input-requests", json=ask_body(), headers=started.headers)
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "RUN_CANCELLED"
    cancelled = {"code": "RUN_CANCELLED", "message": "run cancelled", "retryable": False}
    done = await api.post(
        f"{SDK}/result", json={"status": "failed", "error": cancelled}, headers=started.headers
    )
    assert done.json() == {"state": "CANCELLED", "cancelRequested": True}
    again = await api.post(f"{API}/runs/{started.run_id}/cancel")
    assert again.json()["state"] == "CANCELLED"


async def test_heartbeat_after_the_run_ended_asks_the_agent_to_stop(api: httpx.AsyncClient) -> None:
    started = await start(api)
    await api.post(f"/fake/v1/runs/{started.run_id}/exited", json={"attempt": 1, "exitCode": 1})
    r = await api.post(f"{SDK}/heartbeat", json={}, headers=started.headers)
    assert r.json() == {"state": "INTERRUPTED", "cancelRequested": True}


async def test_exit_without_result_interrupts_and_retry_is_a_new_attempt(
    api: httpx.AsyncClient,
) -> None:
    started = await start(api)
    exited = f"/fake/v1/runs/{started.run_id}/exited"
    r = await api.post(exited, json={"attempt": 1, "exitCode": 137})
    assert r.json()["state"] == "INTERRUPTED"
    run = (await api.get(f"{API}/runs/{started.run_id}")).json()
    assert (run["error"]["code"], run["retryable"]) == ("HEARTBEAT_LOST", True)
    kinds = [e["type"] for e in await events(api, started.run_id)]
    assert kinds[-2:] == ["run.error", "run.state_changed"]
    assert (await api.post(f"{API}/runs/{started.run_id}/retry")).json()["state"] == "QUEUED"
    dispatch = await api.post(
        f"/fake/v1/runs/{started.run_id}/dispatch", json={"brokerUrl": "http://fake"}
    )
    assert dispatch.json()["attempt"] == 2
    assert dispatch.json()["env"]["PLATFORM_ATTEMPT"] == "2"
    old = await api.post(f"{SDK}/heartbeat", json={}, headers=started.headers)
    assert old.status_code == 401
    new_headers = {"Authorization": f"Bearer {dispatch.json()['env']['PLATFORM_RUN_TOKEN']}"}
    hs = await api.post(f"{SDK}/handshake", json=HANDSHAKE, headers=new_headers)
    assert hs.json()["run"]["attempt"] == 2


async def test_exit_after_result_keeps_state(api: httpx.AsyncClient) -> None:
    started = await start(api)
    await api.post(
        f"{SDK}/result", json={"status": "succeeded", "result": {}}, headers=started.headers
    )
    r = await api.post(f"/fake/v1/runs/{started.run_id}/exited", json={"attempt": 1, "exitCode": 0})
    assert r.json()["state"] == "SUCCEEDED"


async def test_unapproved_capability_is_denied_and_audited(api: httpx.AsyncClient) -> None:
    no_input = manifest(userInput=False)
    no_input["spec"]["resources"]["maxInputWaitSeconds"] = 0
    started = await start(api, no_input)
    r = await api.post(f"{SDK}/input-requests", json=ask_body(), headers=started.headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CAPABILITY_DENIED"
    [denied] = [a for a in await audit(api) if a["action"] == "capability.denied"]
    assert denied["capability"] == "user_input"
    assert denied["operation"] == "broker.input.create"
    # Denials are audit records, not run events.
    assert all(e["type"].startswith("run.") for e in await events(api, started.run_id))


async def test_input_request_lifecycle(api: httpx.AsyncClient) -> None:
    started = await start(api)
    created = await api.post(f"{SDK}/input-requests", json=ask_body(), headers=started.headers)
    request = created.json()
    assert (request["state"], request["version"], request["key"]) == ("pending", 1, "confirm-v1")
    assert await run_state(api, started.run_id) == "WAITING_INPUT"
    same = await api.post(f"{SDK}/input-requests", json=ask_body(), headers=started.headers)
    assert same.json()["id"] == request["id"]

    poll = asyncio.create_task(
        api.get(
            f"{SDK}/input-requests/{request['id']}", params={"wait": 5}, headers=started.headers
        )
    )
    await asyncio.sleep(0.05)
    pending = (await api.get(f"{API}/input-requests", params={"state": "pending"})).json()
    [item] = pending["items"]
    assert (item["key"], item["runId"]) == ("confirm-v1", started.run_id)
    assert item["preview"]["choices"][0]["label"] == "Yes"
    answer = await api.post(
        f"{API}/input-requests/{request['id']}/answer",
        json={"version": 1, "value": {"choice": "yes"}},
    )
    assert answer.status_code == 200, answer.text
    polled = (await asyncio.wait_for(poll, 2)).json()
    assert polled["state"] == "answered"
    assert polled["answer"] == {"choice": "yes"}
    assert polled["answeredAt"] is not None
    assert await run_state(api, started.run_id) == "RUNNING"
    kinds = [e["type"] for e in await events(api, started.run_id)]
    assert "run.input_requested" in kinds and "run.input_answered" in kinds


async def test_asking_an_answered_key_again_returns_the_answer(api: httpx.AsyncClient) -> None:
    started = await start(api)
    request = (
        await api.post(f"{SDK}/input-requests", json=ask_body(), headers=started.headers)
    ).json()
    await api.post(
        f"{API}/input-requests/{request['id']}/answer",
        json={"version": 1, "value": {"choice": "no"}},
    )
    again = await api.post(
        f"{SDK}/input-requests", json=ask_body(prompt="Different?"), headers=started.headers
    )
    assert (again.json()["id"], again.json()["state"]) == (request["id"], "answered")
    assert await run_state(api, started.run_id) == "RUNNING"


async def test_input_timeout_above_budget_is_rejected(api: httpx.AsyncClient) -> None:
    started = await start(api)
    r = await api.post(
        f"{SDK}/input-requests", json=ask_body(timeout=3601), headers=started.headers
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INPUT_WAIT_BUDGET_EXCEEDED"


async def test_input_schema_may_not_use_patterns(api: httpx.AsyncClient) -> None:
    started = await start(api)
    body = ask_body()
    body["schema"] = {"type": "object", "properties": {"x": {"type": "string", "pattern": "a+"}}}
    r = await api.post(f"{SDK}/input-requests", json=body, headers=started.headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_INPUT_SCHEMA"


async def test_invalid_input_keys_are_rejected(api: httpx.AsyncClient) -> None:
    started = await start(api)
    r = await api.post(
        f"{SDK}/input-requests", json=ask_body(key="has space"), headers=started.headers
    )
    assert r.status_code == 422


async def test_answer_checks_version_schema_and_state(api: httpx.AsyncClient) -> None:
    started = await start(api)
    request = (
        await api.post(f"{SDK}/input-requests", json=ask_body(), headers=started.headers)
    ).json()
    url = f"{API}/input-requests/{request['id']}/answer"
    stale = await api.post(url, json={"version": 9, "value": {"choice": "yes"}})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
    invalid = await api.post(url, json={"version": 1, "value": {"choice": "maybe"}})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_ANSWER"
    ok = await api.post(url, json={"version": 1, "value": {"choice": "no"}})
    assert ok.json()["state"] == "answered"
    twice = await api.post(url, json={"version": 2, "value": {"choice": "no"}})
    assert twice.status_code == 409
    assert twice.json()["error"]["code"] == "INPUT_ALREADY_CLOSED"


async def test_input_expires_at_its_deadline(api: httpx.AsyncClient) -> None:
    started = await start(api)
    request = (
        await api.post(f"{SDK}/input-requests", json=ask_body(timeout=1), headers=started.headers)
    ).json()
    polled = await api.get(
        f"{SDK}/input-requests/{request['id']}", params={"wait": 3}, headers=started.headers
    )
    assert polled.json()["state"] == "expired"
    assert await run_state(api, started.run_id) == "RUNNING"


async def test_input_poll_is_scoped_to_the_run(api: httpx.AsyncClient) -> None:
    first = await start(api)
    request = (
        await api.post(f"{SDK}/input-requests", json=ask_body(), headers=first.headers)
    ).json()
    other_manifest = manifest(userInput=True)
    other_manifest["metadata"]["id"] = "other-agent"
    other = await start(api, other_manifest)
    r = await api.get(f"{SDK}/input-requests/{request['id']}", headers=other.headers)
    assert r.status_code == 404


async def test_auto_answer_answers_matching_requests(api: httpx.AsyncClient) -> None:
    started = await start(api)
    await api.post(
        "/fake/v1/auto-answers", json={"keyPattern": "confirm-*", "value": {"choice": "yes"}}
    )
    request = (
        await api.post(f"{SDK}/input-requests", json=ask_body(), headers=started.headers)
    ).json()
    polled = await api.get(
        f"{SDK}/input-requests/{request['id']}", params={"wait": 3}, headers=started.headers
    )
    assert (polled.json()["state"], polled.json()["answer"]) == ("answered", {"choice": "yes"})


async def test_cancel_marks_pending_inputs_cancelled(api: httpx.AsyncClient) -> None:
    started = await start(api)
    request = (
        await api.post(f"{SDK}/input-requests", json=ask_body(), headers=started.headers)
    ).json()
    await api.post(f"{API}/runs/{started.run_id}/cancel")
    listing = (await api.get(f"{API}/input-requests", params={"state": "all"})).json()["items"]
    assert [(i["id"], i["state"]) for i in listing] == [(request["id"], "cancelled")]


async def test_actions_claim_in_doubt_and_complete(api: httpx.AsyncClient) -> None:
    started = await start(api)
    claim_url = f"{SDK}/actions/call%3A1/claim"
    complete_url = f"{SDK}/actions/call%3A1/complete"
    claim = await api.post(claim_url, headers=started.headers)
    assert claim.json() == {"key": "call:1", "status": "claimed", "result": None}
    # A second claim of an uncompleted key, from this attempt or a retry, is in doubt.
    again = await api.post(claim_url, headers=started.headers)
    assert again.json()["status"] == "in_doubt"

    await api.post(f"/fake/v1/runs/{started.run_id}/exited", json={"attempt": 1, "exitCode": 1})
    await api.post(f"{API}/runs/{started.run_id}/retry")
    dispatch = (
        await api.post(f"/fake/v1/runs/{started.run_id}/dispatch", json={"brokerUrl": "x"})
    ).json()
    headers = {"Authorization": f"Bearer {dispatch['env']['PLATFORM_RUN_TOKEN']}"}
    await api.post(f"{SDK}/handshake", json=HANDSHAKE, headers=headers)

    assert (await api.post(claim_url, headers=headers)).json()["status"] == "in_doubt"
    done = await api.post(complete_url, json={"result": {"sid": "CA1"}}, headers=headers)
    assert done.json() == {"key": "call:1", "status": "completed", "result": {"sid": "CA1"}}
    replay = await api.post(claim_url, headers=headers)
    assert replay.json() == {"key": "call:1", "status": "completed", "result": {"sid": "CA1"}}
    missing = await api.post(f"{SDK}/actions/nope/complete", json={}, headers=headers)
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "ACTION_NOT_CLAIMED"


async def test_fault_injection_applies_to_broker_operations(api: httpx.AsyncClient) -> None:
    started = await start(api)
    await api.post(
        "/fake/v1/faults", json={"target": "broker.heartbeat", "mode": "error", "status": 503}
    )
    failed = await api.post(f"{SDK}/heartbeat", json={}, headers=started.headers)
    assert failed.status_code == 503
    ok = await api.post(f"{SDK}/heartbeat", json={}, headers=started.headers)
    assert ok.status_code == 200
