"""Connector-call auditing and connection checks for broker connector routes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request

from crewquarters_fake.broker.auth import RunAuth
from crewquarters_fake.errors import ApiError
from crewquarters_fake.store import new_id


def require_connection(auth: RunAuth, provider: str) -> None:
    status = auth.store.connections.get(provider, "missing")
    if status != "connected":
        raise ApiError(
            409,
            "NEEDS_CONNECTION",
            f"the {provider} connection is {status}; reconnect it in Connections",
            {"provider": provider, "status": status},
        )


def request_id(request: Request) -> str:
    return request.headers.get("x-request-id") or new_id("req")


async def audited[T](
    auth: RunAuth,
    request: Request,
    connector: str,
    operation: str,
    call: Callable[[], Awaitable[T]],
) -> T:
    """Run a connector operation through fault injection and record a payload-free connector.call event."""
    payload = {"connector": connector, "operation": operation, "requestId": request_id(request)}
    try:
        result = await auth.store.faults.run(operation, call)
    except ApiError:
        auth.store.append_event(auth.run, "connector.call", {**payload, "outcome": "error"})
        await auth.store.notify()
        raise
    auth.store.append_event(auth.run, "connector.call", {**payload, "outcome": "ok"})
    await auth.store.notify()
    return result
