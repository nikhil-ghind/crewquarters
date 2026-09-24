"""Synchronous client for the fake platform's control and admin APIs."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx


class FakePlatformError(Exception):
    def __init__(self, status: int, body: Any) -> None:
        error = body.get("error", {}) if isinstance(body, dict) else {}
        self.status = status
        self.code = error.get("code")
        self.body = body
        super().__init__(f"HTTP {status} {self.code}: {error.get('message', body)}")


class FakePlatformClient:
    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(method, path, **kwargs)
        body = response.json() if response.content else None
        if response.status_code >= 400:
            raise FakePlatformError(response.status_code, body)
        return body

    # --- admin ---------------------------------------------------------------------------------
    def reset(self) -> None:
        self._call("POST", "/fake/v1/reset")

    def load_scenario(self, path: str, *, now: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"path": str(path)}
        if now is not None:
            body["now"] = now
        result: dict[str, Any] = self._call("POST", "/fake/v1/scenarios/load", json=body)
        return result

    def register_manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = self._call("POST", "/fake/v1/catalog", json={"manifest": manifest})
        return result

    def dispatch(self, run_id: str, broker_url: str) -> dict[str, Any]:
        result: dict[str, Any] = self._call(
            "POST", f"/fake/v1/runs/{run_id}/dispatch", json={"brokerUrl": broker_url}
        )
        return result

    def report_exit(self, run_id: str, attempt: int, exit_code: int) -> dict[str, Any]:
        body = {"attempt": attempt, "exitCode": exit_code}
        result: dict[str, Any] = self._call("POST", f"/fake/v1/runs/{run_id}/exited", json=body)
        return result

    def add_fault(
        self,
        target: str,
        mode: str,
        *,
        count: int = 1,
        status: int | None = None,
        code: str | None = None,
        delay_ms: int = 0,
    ) -> None:
        body = {
            "target": target,
            "mode": mode,
            "count": count,
            "status": status,
            "code": code,
            "delayMs": delay_ms,
        }
        self._call("POST", "/fake/v1/faults", json=body)

    def clear_faults(self) -> None:
        self._call("DELETE", "/fake/v1/faults")

    def add_auto_answer(self, key_pattern: str, data: Any, delay_seconds: float = 0.0) -> None:
        body = {"keyPattern": key_pattern, "data": data, "delaySeconds": delay_seconds}
        self._call("POST", "/fake/v1/auto-answers", json=body)

    def set_connection(self, provider: str, status: str) -> None:
        self._call("POST", "/fake/v1/connections", json={"provider": provider, "status": status})

    def state(self, kind: str) -> Any:
        return self._call("GET", f"/fake/v1/state/{kind}")

    # --- control API ---------------------------------------------------------------------------
    def import_manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = self._call(
            "POST", "/api/v1/catalog/agents:import", json={"manifest": manifest}
        )
        return result

    def install(
        self,
        agent_id: str,
        version: str,
        config: dict[str, Any],
        approved_permissions: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = {
            "agentId": agent_id,
            "version": version,
            "config": config,
            "approvedPermissions": approved_permissions,
        }
        result: dict[str, Any] = self._call("POST", "/api/v1/agent-installations", json=body)
        return result

    def create_run(
        self,
        installation_id: str,
        *,
        trigger: str = "manual",
        scheduled_for: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        body = {"installationId": installation_id, "trigger": trigger, "scheduledFor": scheduled_for}
        result: dict[str, Any] = self._call("POST", "/api/v1/runs", json=body, headers=headers)
        return result

    def get_run(self, run_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._call("GET", f"/api/v1/runs/{run_id}")
        return result

    def events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        body = self._call("GET", f"/api/v1/runs/{run_id}/events", params={"after": after})
        return list(body["items"])

    def input_requests(self, state: str | None = None) -> list[dict[str, Any]]:
        params = {"state": state} if state else {}
        return list(self._call("GET", "/api/v1/input-requests", params=params)["items"])

    def answer(self, request_id: str, version: int, data: Any) -> dict[str, Any]:
        body = {"version": version, "data": data}
        result: dict[str, Any] = self._call("POST", f"/api/v1/input-requests/{request_id}/answer", json=body)
        return result

    def cancel(self, run_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._call("POST", f"/api/v1/runs/{run_id}/cancel")
        return result

    def retry(self, run_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._call("POST", f"/api/v1/runs/{run_id}/retry")
        return result

    # --- helpers -------------------------------------------------------------------------------
    def wait_for(self, predicate: Callable[[], bool], timeout: float, interval: float = 0.05) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise TimeoutError("condition not met before the timeout")
            time.sleep(interval)
