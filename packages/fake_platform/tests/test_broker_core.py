import asyncio
from typing import Any

import httpx
from helpers import SDK, events, manifest, run_state, start

API = "/api/v1"


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
        "choices": [{"value": "yes", "label": "Yes", "style": "primary"}, {"value": "no", "label": "No"}],
        "timeoutSeconds": timeout,
        **extra,
    }


async def test_handshake_moves_run_to_running_and_returns_context(api: httpx.AsyncClient) -> None:
    started = await start(api, handshake=False)
    assert await run_state(api, started.run_id) == "PREPARING"
    r = await api.post(
        f"{SDK}/handshake",
        json={"protocol": "v1alpha1", "sdkVersion": "0.1.0", "agentId": "probe"},
        headers=started.headers,
    )
    body = r.json()
    assert body["run"]["id"] == started.run_id
    assert body["run"]["attempt"] == 1
    assert body["config"] == {"timezone": "UTC", "limit": 5}
    assert body["capabilities"] == ["input.ask", "llm.local"]
    assert body["grants"]["llmProfiles"] == ["local.general.small"]
    assert body["limits"] == {"activeTimeoutSeconds": 600, "inputWaitRemainingSeconds": 3600}
    assert body["heartbeatIntervalSeconds"] == 0.05
    assert await run_state(api, started.run_id) == "RUNNING"
    statuses = [e["payload"]["to"] for e in await events(api, started.run_id) if e["type"] == "status"]
    assert statuses == ["PREPARING", "RUNNING"]


async def test_handshake_rejects_other_protocols(api: httpx.AsyncClient) -> None:
    started = await start(api, handshake=False)
    r = await api.post(
        f"{SDK}/handshake",
        json={"protocol": "v2", "sdkVersion": "x", "agentId": "probe"},
        headers=started.headers,
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
            {
                "clientEventId": "a",
                "type": "log",
                "occurredAt": "2026-09-24T10:00:00Z",
                "payload": {"level": "info", "message": "x"},
            },
            {
                "clientEventId": "b",
                "type": "progress",
                "occurredAt": "2026-09-24T10:00:00Z",
                "payload": {"percent": 50, "message": "half"},
            },
        ]
    }
    first = await api.post(f"{SDK}/events", json=batch, headers=started.headers)
    again = await api.post(f"{SDK}/events", json=batch, headers=started.headers)
    assert first.json()["accepted"] == 2
    assert again.json()["accepted"] == 0
    agent_events = [e for e in await events(api, started.run_id) if e["type"] in {"log", "progress"}]
    assert [e["payload"]["message"] for e in agent_events] == ["x", "half"]
    sequences = [e["sequence"] for e in await events(api, started.run_id)]
    assert sequences == sorted(sequences) == list(range(1, len(sequences) + 1))


async def test_agents_cannot_emit_platform_event_types(api: httpx.AsyncClient) -> None:
    started = await start(api)
    batch = {"events": [{"clientEventId": "a", "type": "status", "occurredAt": "x", "payload": {}}]}
    r = await api.post(f"{SDK}/events", json=batch, headers=started.headers)
    assert r.status_code == 422


async def test_result_succeeds_and_is_idempotent(api: httpx.AsyncClient) -> None:
    started = await start(api)
    body = {"outcome": "succeeded", "result": {"n": 1}}
    first = await api.post(f"{SDK}/result", json=body, headers=started.headers)
    again = await api.post(f"{SDK}/result", json=body, headers=started.headers)
    assert first.json() == again.json() == {"runState": "SUCCEEDED"}
    other = await api.post(f"{SDK}/result", json={"outcome": "failed", "error": {}}, headers=started.headers)
    assert other.status_code == 409
    assert other.json()["error"]["code"] == "OUTCOME_ALREADY_RECORDED"
    run = (await api.get(f"{API}/runs/{started.run_id}")).json()
    assert run["result"] == {"n": 1}


async def test_failed_result_records_error(api: httpx.AsyncClient) -> None:
    started = await start(api)
    error = {"code": "AGENT_ERROR", "message": "boom", "retryable": True}
    r = await api.post(f"{SDK}/result", json={"outcome": "failed", "error": error}, headers=started.headers)
    assert r.json() == {"runState": "FAILED"}
    run = (await api.get(f"{API}/runs/{started.run_id}")).json()
    assert run["error"]["code"] == "AGENT_ERROR"
    retried = await api.post(f"{API}/runs/{started.run_id}/retry")
    assert retried.json()["state"] == "QUEUED"


async def test_non_retryable_failure_cannot_be_retried(api: httpx.AsyncClient) -> None:
    started = await start(api)
    error = {"code": "CONFIG_INVALID", "message": "bad", "retryable": False}
    await api.post(f"{SDK}/result", json={"outcome": "failed", "error": error}, headers=started.headers)
    r = await api.post(f"{API}/runs/{started.run_id}/retry")
    assert r.status_code == 409


async def test_cancel_running_run_goes_through_cancelling(api: httpx.AsyncClient) -> None:
    started = await start(api)
    r = await api.post(f"{API}/runs/{started.run_id}/cancel")
    assert r.json()["state"] == "CANCELLING"
    heartbeat = await api.post(f"{SDK}/heartbeat", json={}, headers=started.headers)
    assert heartbeat.json()["cancelRequested"] is True
    denied = await api.post(f"{SDK}/input-requests", json=ask_body(), headers=started.headers)
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "RUN_CANCELLED"
    done = await api.post(f"{SDK}/result", json={"outcome": "cancelled"}, headers=started.headers)
    assert done.json() == {"runState": "CANCELLED"}


async def test_exit_without_outcome_interrupts_and_retry_creates_new_attempt(api: httpx.AsyncClient) -> None:
    started = await start(api)
    r = await api.post(f"/fake/v1/runs/{started.run_id}/exited", json={"attempt": 1, "exitCode": 137})
    assert r.json()["state"] == "INTERRUPTED"
    run = (await api.get(f"{API}/runs/{started.run_id}")).json()
    assert run["error"]["code"] == "CONTAINER_EXITED"
    assert (await api.post(f"{API}/runs/{started.run_id}/retry")).json()["state"] == "QUEUED"
    dispatch = await api.post(f"/fake/v1/runs/{started.run_id}/dispatch", json={"brokerUrl": "http://fake"})
    assert dispatch.json()["attempt"] == 2
    old = await api.post(f"{SDK}/heartbeat", json={}, headers=started.headers)
    assert old.status_code == 401
    new_headers = {"Authorization": f"Bearer {dispatch.json()['env']['PLATFORM_RUN_TOKEN']}"}
    hs = await api.post(
        f"{SDK}/handshake",
        json={"protocol": "v1alpha1", "sdkVersion": "t", "agentId": "probe"},
        headers=new_headers,
    )
    assert hs.json()["run"]["attempt"] == 2


async def test_exit_after_outcome_keeps_state(api: httpx.AsyncClient) -> None:
    started = await start(api)
    await api.post(f"{SDK}/result", json={"outcome": "succeeded", "result": {}}, headers=started.headers)
    r = await api.post(f"/fake/v1/runs/{started.run_id}/exited", json={"attempt": 1, "exitCode": 0})
    assert r.json()["state"] == "SUCCEEDED"


async def test_undeclared_capability_is_denied_and_audited(api: httpx.AsyncClient) -> None:
    no_input = manifest(userInput=False)
    no_input["spec"]["resources"]["maxInputWaitSeconds"] = 0
    started = await start(api, no_input)
    r = await api.post(f"{SDK}/input-requests", json=ask_body(), headers=started.headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CAPABILITY_DENIED"
    denied = [e for e in await events(api, started.run_id) if e["type"] == "capability.denied"]
    assert denied[0]["payload"] == {"capability": "input.ask", "operation": "broker.input.create"}


async def test_input_request_lifecycle(api: httpx.AsyncClient) -> None:
    started = await start(api)
    created = await api.post(f"{SDK}/input-requests", json=ask_body(), headers=started.headers)
    request = created.json()
    assert request["state"] == "pending"
    assert await run_state(api, started.run_id) == "WAITING_INPUT"
    same = await api.post(f"{SDK}/input-requests", json=ask_body(), headers=started.headers)
    assert same.json()["id"] == request["id"]

    poll = asyncio.create_task(
        api.get(f"{SDK}/input-requests/confirm-v1", params={"waitSeconds": 5}, headers=started.headers)
    )
    await asyncio.sleep(0.05)
    pending = (await api.get(f"{API}/input-requests", params={"state": "pending"})).json()["items"]
    assert [p["key"] for p in pending] == ["confirm-v1"]
    assert pending[0]["runId"] == started.run_id
    assert pending[0]["choices"][0]["label"] == "Yes"
    answer = await api.post(
        f"{API}/input-requests/{request['id']}/answer", json={"version": 1, "data": {"choice": "yes"}}
    )
    assert answer.status_code == 200, answer.text
    polled = (await asyncio.wait_for(poll, 2)).json()
    assert polled["state"] == "answered"
    assert polled["answer"]["data"] == {"choice": "yes"}
    assert await run_state(api, started.run_id) == "RUNNING"
    types = [e["type"] for e in await events(api, started.run_id)]
    assert "input.requested" in types and "input.answered" in types


async def test_same_key_with_different_content_conflicts(api: httpx.AsyncClient) -> None:
    started = await start(api)
    await api.post(f"{SDK}/input-requests", json=ask_body(), headers=started.headers)
    r = await api.post(f"{SDK}/input-requests", json=ask_body(prompt="Different?"), headers=started.headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INPUT_KEY_CONFLICT"


async def test_input_timeout_above_budget_is_rejected(api: httpx.AsyncClient) -> None:
    started = await start(api)
    r = await api.post(f"{SDK}/input-requests", json=ask_body(timeout=3601), headers=started.headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INPUT_WAIT_BUDGET_EXCEEDED"


async def test_answer_version_conflict_and_schema_validation(api: httpx.AsyncClient) -> None:
    started = await start(api)
    request = (await api.post(f"{SDK}/input-requests", json=ask_body(), headers=started.headers)).json()
    stale = await api.post(
        f"{API}/input-requests/{request['id']}/answer", json={"version": 9, "data": {"choice": "yes"}}
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
    invalid = await api.post(
        f"{API}/input-requests/{request['id']}/answer", json={"version": 1, "data": {"choice": "maybe"}}
    )
    assert invalid.status_code == 422
    ok = await api.post(
        f"{API}/input-requests/{request['id']}/answer", json={"version": 1, "data": {"choice": "no"}}
    )
    assert ok.json()["state"] == "answered"
    twice = await api.post(
        f"{API}/input-requests/{request['id']}/answer", json={"version": 2, "data": {"choice": "no"}}
    )
    assert twice.status_code == 409
    assert twice.json()["error"]["code"] == "ALREADY_ANSWERED"


async def test_input_expires_at_its_deadline(api: httpx.AsyncClient) -> None:
    started = await start(api)
    await api.post(f"{SDK}/input-requests", json=ask_body(timeout=1), headers=started.headers)
    polled = await api.get(
        f"{SDK}/input-requests/confirm-v1", params={"waitSeconds": 3}, headers=started.headers
    )
    assert polled.json()["state"] == "expired"
    assert await run_state(api, started.run_id) == "RUNNING"


async def test_auto_answer_answers_matching_requests(api: httpx.AsyncClient) -> None:
    started = await start(api)
    await api.post("/fake/v1/auto-answers", json={"keyPattern": "confirm-*", "data": {"choice": "yes"}})
    await api.post(f"{SDK}/input-requests", json=ask_body(), headers=started.headers)
    polled = await api.get(
        f"{SDK}/input-requests/confirm-v1", params={"waitSeconds": 3}, headers=started.headers
    )
    assert polled.json()["state"] == "answered"
    assert polled.json()["answer"]["answeredBy"] == "auto-answer"


async def test_cancel_marks_pending_inputs_cancelled(api: httpx.AsyncClient) -> None:
    started = await start(api)
    request = (await api.post(f"{SDK}/input-requests", json=ask_body(), headers=started.headers)).json()
    await api.post(f"{API}/runs/{started.run_id}/cancel")
    listing = (await api.get(f"{API}/input-requests")).json()["items"]
    assert [(i["id"], i["state"]) for i in listing] == [(request["id"], "cancelled")]


async def test_idempotency_claim_complete_and_takeover(api: httpx.AsyncClient) -> None:
    started = await start(api)
    claim = await api.post(f"{SDK}/idempotency/claim", json={"key": "call:1"}, headers=started.headers)
    assert claim.json()["state"] == "claimed"
    assert claim.json()["claimedByAttempt"] == 1
    reclaim = await api.post(f"{SDK}/idempotency/claim", json={"key": "call:1"}, headers=started.headers)
    assert reclaim.json()["state"] == "claimed"

    await api.post(f"/fake/v1/runs/{started.run_id}/exited", json={"attempt": 1, "exitCode": 1})
    await api.post(f"{API}/runs/{started.run_id}/retry")
    dispatch = (await api.post(f"/fake/v1/runs/{started.run_id}/dispatch", json={"brokerUrl": "x"})).json()
    headers = {"Authorization": f"Bearer {dispatch['env']['PLATFORM_RUN_TOKEN']}"}
    await api.post(
        f"{SDK}/handshake", json={"protocol": "v1alpha1", "sdkVersion": "t", "agentId": "p"}, headers=headers
    )

    in_progress = await api.post(f"{SDK}/idempotency/claim", json={"key": "call:1"}, headers=headers)
    assert in_progress.json()["state"] == "in_progress"
    early = await api.post(
        f"{SDK}/idempotency/complete", json={"key": "call:1", "result": 1}, headers=headers
    )
    assert early.status_code == 409
    taken = await api.post(
        f"{SDK}/idempotency/claim", json={"key": "call:1", "takeover": True}, headers=headers
    )
    assert taken.json()["state"] == "claimed"
    assert taken.json()["claimedByAttempt"] == 2
    done = await api.post(
        f"{SDK}/idempotency/complete", json={"key": "call:1", "result": {"sid": "CA1"}}, headers=headers
    )
    assert done.json()["state"] == "completed"
    record = await api.get(f"{SDK}/idempotency/call%3A1", headers=headers)
    assert record.json()["result"] == {"sid": "CA1"}
    missing = await api.get(f"{SDK}/idempotency/nope", headers=headers)
    assert missing.status_code == 404


async def test_fault_injection_applies_to_broker_operations(api: httpx.AsyncClient) -> None:
    started = await start(api)
    await api.post("/fake/v1/faults", json={"target": "broker.heartbeat", "mode": "error", "status": 503})
    failed = await api.post(f"{SDK}/heartbeat", json={}, headers=started.headers)
    assert failed.status_code == 503
    ok = await api.post(f"{SDK}/heartbeat", json={}, headers=started.headers)
    assert ok.status_code == 200
