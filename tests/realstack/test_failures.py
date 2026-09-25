"""Cancellation and failure injection on the real stack (PLAN.md §21, §24 step 11).

The stack runs with CQ_HEARTBEAT_TIMEOUT_SECONDS=15 (compose.realstack.yaml), so a lost
agent is detected within ~20 s instead of ~35 s. Services stopped here are restarted by the
``healthy`` fixture even when an assertion fails.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

PROBE = "contract-probe"
ADMIN_URL = "http://127.0.0.1:18084"  # the broker's port, published by compose.realstack.yaml
LOST = 60  # heartbeat timeout 15 s + reconciler interval + container stop


def _probe(owner: Any, kb: dict[str, Any], checks: list[str], **config: Any) -> dict[str, Any]:
    return dict(owner.install(PROBE, {"knowledgeBaseId": kb["id"], "checks": checks, **config}))


def _broker_up() -> bool:
    try:
        return httpx.get(f"{ADMIN_URL}/health/ready", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


def _log_seen(owner: Any, run_id: str, message: str) -> bool:
    return any(
        e["type"] == "run.log" and e["payload"].get("message") == message
        for e in owner.events(run_id)
    )


def test_cancel_mid_run_stops_the_container_and_cannot_be_retried(
    owner: Any, stack: Any, knowledge_base: dict[str, Any], healthy: None
) -> None:
    installation = _probe(
        owner, knowledge_base, ["handshake", "llm", "cancellation"], cancelWaitSeconds=300
    )
    run = owner.start(installation["id"])
    owner.wait_for(
        lambda: _log_seen(owner, run["id"], "waiting for cancellation"), "the agent to wait", 90
    )
    container = stack.run_container(run["id"])
    assert stack.inspect(container)["State"]["Status"] == "running"

    cancelling = owner.cancel(run["id"])
    assert cancelling["state"] == "CANCELLING" and cancelling["cancelRequested"] is True
    done = owner.wait_state(run["id"], "CANCELLED", timeout=60)
    assert done["finishedAt"] is not None
    owner.wait_for(lambda: stack.inspect(container) is None, "the container to be removed", 30)
    # SUCCEEDED and CANCELLED are final (ADR 0006): a cancelled run is not retried.
    again = owner.post(f"/api/v1/runs/{run['id']}/retry")
    assert again.status_code == 409, again.text


def _attempt_exit_code(stack: Any, run_id: str, attempt: int = 1) -> str:
    run_uuid, number = uuid.UUID(run_id), int(attempt)  # both validated before interpolation
    rows = stack.psql(
        f"SELECT exit_code FROM run_attempts WHERE run_id = '{run_uuid}' AND attempt = {number}"  # noqa: S608
    )
    return rows[0][0] if rows else "<no attempt>"


def test_out_of_memory_agent_fails_with_a_clear_error(
    owner: Any, stack: Any, healthy: None
) -> None:
    """The agent allocates past its 64 MiB limit and the kernel kills it (exit 137,
    OOMKilled). The scheduler's exit watcher reads that from the runtime daemon and fails the
    run with AGENT_OUT_OF_MEMORY and the limit, instead of waiting for the heartbeat lease
    (ADR 0006, revision 1)."""
    installation = owner.install("realstack-oom", {})
    run = owner.start(installation["id"])
    container = stack.run_container(run["id"])
    failed = owner.wait_state(run["id"], "FAILED", timeout=90)
    error = failed["error"]
    assert error["code"] == "AGENT_OUT_OF_MEMORY", error
    assert "64 MiB memory limit" in error["message"], error
    # oomKilled is Docker's flag, which Docker loses for about 1 in 12 kills; exit code 137
    # alone is then reported as a probable OOM (the message says it was not confirmed).
    oom_flag = error["details"]["oomKilled"]
    assert error["details"] == {"exitCode": 137, "oomKilled": oom_flag, "memoryLimitMb": 64}
    assert oom_flag is True or "did not confirm" in error["message"], error
    assert failed["retryable"] is True
    assert _attempt_exit_code(stack, run["id"]) == "137"
    assert _log_seen(owner, run["id"], "allocating")
    # The exited container is removed by the stop job.
    owner.wait_for(lambda: stack.inspect(container) is None, "the container to be removed", 60)


def test_crash_before_handshake_fails_within_seconds(owner: Any, stack: Any, healthy: None) -> None:
    """The agent exits 3 before its handshake. The heartbeat lease would only notice after the
    prepare timeout (120 s here, 600 s by default); the exit watcher sees it in seconds."""
    installation = owner.install("realstack-crash", {})
    began = time.monotonic()
    run = owner.start(installation["id"])
    failed = owner.wait_state(run["id"], "FAILED", timeout=60)
    took = time.monotonic() - began
    assert took < 30, f"the crash took {took:.1f}s to surface"
    error = failed["error"]
    assert error["code"] == "AGENT_EXITED", error
    assert "exited with code 3 before its handshake" in error["message"], error
    assert error["details"] == {"exitCode": 3, "oomKilled": False}
    assert failed["retryable"] is True and failed["currentAttempt"] == 1
    assert "RUNNING" not in owner.states(run["id"])
    assert _attempt_exit_code(stack, run["id"]) == "3"
    owner.wait_for(
        lambda: stack.inspect(stack.run_container(run["id"])) is None,
        "the container to be removed",
        60,
    )


def test_broker_outage_short_is_absorbed_long_interrupts_and_retry_recovers(
    owner: Any, stack: Any, knowledge_base: dict[str, Any], healthy: None
) -> None:
    installation = _probe(owner, knowledge_base, ["handshake", "input", "llm", "knowledge"])

    # Short outage (a restart, well under the heartbeat timeout): the SDK rides it out. The
    # agent's in-flight long poll for input loses its connection and is retried until the
    # broker answers again (budget: 2.5 heartbeat intervals, 12.5 s with this stack's 15 s
    # timeout), so the run neither loses its attempt nor fails with BROKER_UNAVAILABLE.
    run = owner.start(installation["id"])
    request = owner.pending_input(run["id"])
    down = time.monotonic()
    stack.stop("capability-broker", grace=1)
    time.sleep(3)
    stack.start("capability-broker", wait=False)
    owner.wait_for(_broker_up, "the broker to answer again", 20, 0.2)
    outage = time.monotonic() - down
    assert outage < 10, f"the 'short' outage took {outage:.1f}s"
    answered = owner.answer(request, {"choice": "ok"})
    assert answered.status_code == 200, (answered.text, owner.run(run["id"]))
    ended = owner.wait_state(run["id"], "SUCCEEDED", timeout=120)
    assert ended["currentAttempt"] == 1  # never interrupted

    # Long outage: the attempt loses its heartbeat lease.
    run = owner.start(installation["id"])
    owner.pending_input(run["id"])
    stack.stop("capability-broker")
    try:
        interrupted = owner.wait_state(run["id"], "INTERRUPTED", timeout=LOST)
    finally:
        stack.start("capability-broker")
    assert interrupted["error"]["code"] == "HEARTBEAT_LOST" and interrupted["retryable"]
    owner.wait_for(
        lambda: (
            stack.inspect(stack.run_container(run["id"], 1)) is None
            or stack.inspect(stack.run_container(run["id"], 1))["State"]["Status"] == "exited"
        ),
        "attempt 1's container to stop",
        60,
    )

    # The owner retries once the broker is back: a new attempt, a new container; the same
    # input key is asked again and answered.
    retried = owner.retry(run["id"])
    assert retried["currentAttempt"] == 2
    request = owner.pending_input(run["id"])
    assert request["key"] == "probe-input-v1"
    assert owner.answer(request, {"choice": "ok"}).status_code == 200
    done = owner.wait_state(run["id"], "SUCCEEDED", timeout=120)
    assert done["currentAttempt"] == 2
    assert stack.run_containers(run["id"]) in (
        [],
        [stack.run_container(run["id"], 2)],
        sorted([stack.run_container(run["id"], 1), stack.run_container(run["id"], 2)]),
    )


def test_model_gateway_down_fails_llm_calls_visibly(
    owner: Any, stack: Any, knowledge_base: dict[str, Any], healthy: None
) -> None:
    installation = _probe(owner, knowledge_base, ["handshake", "input", "llm", "knowledge"])
    run = owner.start(installation["id"])
    request = owner.pending_input(run["id"])
    stack.stop("model-gateway")
    try:
        assert owner.answer(request, {"choice": "ok"}).status_code == 200
        failed = owner.wait_state(run["id"], "FAILED", timeout=180)
    finally:
        stack.start("model-gateway")
    assert failed["error"]["code"] == "CONTRACT_CHECKS_FAILED", failed["error"]
    checks = {c["name"]: c for c in failed["error"]["details"]["checks"]}
    assert checks["llm"]["status"] == "failed"
    assert checks["llm"]["detail"].startswith(("MODEL_UNAVAILABLE", "PROVIDER_UNAVAILABLE")), (
        checks["llm"]
    )
    # Only the LLM failed: the knowledge service answered as usual.
    assert checks["knowledge"]["status"] == "passed" and checks["input"]["status"] == "passed"

    # With the gateway back, the same installation works again.
    run = owner.start(installation["id"])
    assert owner.answer(owner.pending_input(run["id"]), {"choice": "ok"}).status_code == 200
    owner.wait_state(run["id"], "SUCCEEDED", timeout=180)


def test_worker_restart_mid_run_keeps_one_container_and_the_run_completes(
    owner: Any, stack: Any, knowledge_base: dict[str, Any], healthy: None
) -> None:
    """ADR 0006: a worker crash neither loses nor duplicates a run's container."""
    installation = _probe(owner, knowledge_base, ["handshake", "input"])

    # Killed while the agent waits for input: the agent keeps running without the worker.
    run = owner.start(installation["id"])
    request = owner.pending_input(run["id"])
    stack.kill("scheduler")
    try:
        assert owner.answer(request, {"choice": "ok"}).status_code == 200
        done = owner.wait_state(run["id"], "SUCCEEDED", timeout=60)
    finally:
        stack.start("scheduler")
    assert done["currentAttempt"] == 1
    assert stack.run_containers(run["id"]) in ([], [stack.run_container(run["id"])])

    # Created while no worker runs: queued, then dispatched exactly once after the restart.
    stack.kill("scheduler")
    try:
        run = owner.start(installation["id"])
        time.sleep(3)
        assert owner.run(run["id"])["state"] == "QUEUED"
        assert stack.run_containers(run["id"]) == []
    finally:
        stack.start("scheduler")
    request = owner.pending_input(run["id"])
    # Killed again right after dispatch, then restarted: the reclaimed dispatch job must
    # not start a second container for the same attempt.
    stack.kill("scheduler")
    stack.start("scheduler")
    assert owner.answer(request, {"choice": "ok"}).status_code == 200
    done = owner.wait_state(run["id"], "SUCCEEDED", timeout=120)
    assert done["currentAttempt"] == 1
    assert stack.run_containers(run["id"]) in ([], [stack.run_container(run["id"])])
