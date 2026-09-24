"""Agent definition and the run lifecycle (spec section 5.2)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import traceback
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import suppress
from typing import Any, NoReturn, TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from pydantic_core import to_jsonable_python

from crewquarters._transport import BrokerClient
from crewquarters._version import PROTOCOL, __version__
from crewquarters.context import Grants, Limits, RunContext, RunInfo
from crewquarters.errors import Cancelled, PlatformError
from crewquarters.events import EventsClient
from crewquarters.redact import redact_text, redact_value

EXIT_SUCCEEDED = 0
EXIT_NOT_SUCCEEDED = 1
EXIT_NO_OUTCOME = 2
ENV_VARS = ("PLATFORM_BROKER_URL", "PLATFORM_RUN_TOKEN", "PLATFORM_RUN_ID")

AgentFn = Callable[[RunContext[Any]], Coroutine[Any, Any, Any]]
F = TypeVar("F", bound=AgentFn)


def _stderr(message: str) -> None:
    print(" ".join(message.split()), file=sys.stderr, flush=True)


class Agent:
    """An agent: an id, optional config/result models, and one async run function."""

    def __init__(
        self,
        id: str,
        *,
        config_model: type[BaseModel] | None = None,
        result_model: type[BaseModel] | None = None,
    ) -> None:
        self.id = id
        self.config_model = config_model
        self.result_model = result_model
        self._fn: AgentFn | None = None

    def run(self, fn: F) -> F:
        self._fn = fn
        return fn

    def serve(self, argv: list[str] | None = None) -> NoReturn:
        args = sys.argv[1:] if argv is None else argv
        if "--self-check" in args:
            print(json.dumps({"sdk": __version__, "protocol": PROTOCOL, "agent": self.id}))
            sys.exit(0)
        missing = [name for name in ENV_VARS if not os.environ.get(name)]
        if missing:
            _stderr(
                f"crewquarters: missing environment variables {', '.join(missing)}; the platform sets them"
            )
            sys.exit(EXIT_NO_OUTCOME)
        try:
            code = asyncio.run(
                self.execute(
                    broker_url=os.environ["PLATFORM_BROKER_URL"],
                    token=os.environ["PLATFORM_RUN_TOKEN"],
                    run_id=os.environ["PLATFORM_RUN_ID"],
                    install_signal_handlers=True,
                )
            )
        except KeyboardInterrupt:
            code = EXIT_NO_OUTCOME
        except Exception as exc:
            _stderr(f"crewquarters: internal SDK error: {exc!r}")
            code = EXIT_NO_OUTCOME
        sys.exit(code)

    async def execute(
        self,
        *,
        broker_url: str,
        token: str,
        run_id: str,
        http: httpx.AsyncClient | None = None,
        install_signal_handlers: bool = False,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> int:
        """Run one attempt against the broker and return the process exit code."""
        if self._fn is None:
            raise RuntimeError(f"agent {self.id} has no @agent.run function")
        transport = BrokerClient(broker_url, token, http=http, sleep=sleep)
        try:
            try:
                handshake = await transport.request(
                    "POST",
                    "/handshake",
                    operation="handshake",
                    idempotent=True,
                    json={"protocol": PROTOCOL, "sdkVersion": __version__, "agentId": self.id},
                )
            except (PlatformError, Cancelled) as exc:
                _stderr(f"crewquarters: handshake failed: {exc}")
                return EXIT_NO_OUTCOME
            run = RunInfo.from_wire(handshake["run"])
            if run.id != run_id:
                _stderr(f"crewquarters: PLATFORM_RUN_ID {run_id} does not match the broker's run {run.id}")
            events = EventsClient(transport)
            try:
                config = self._parse_config(handshake.get("config") or {})
            except ValidationError as exc:
                error = {
                    "code": "CONFIG_INVALID",
                    "message": redact_text(str(exc))[:2000],
                    "retryable": False,
                }
                return await self._finish(transport, events, {"outcome": "failed", "error": error})

            ctx: RunContext[Any] = RunContext(
                run=run,
                config=config,
                capabilities=frozenset(handshake.get("capabilities", [])),
                grants=Grants.from_wire(handshake.get("grants", {})),
                limits=Limits.from_wire(handshake.get("limits", {})),
                transport=transport,
                events=events,
            )
            events.start()
            task: asyncio.Task[Any] = asyncio.create_task(self._fn(ctx))
            if install_signal_handlers:
                _install_signal_handlers(task)
            interval = float(handshake.get("heartbeatIntervalSeconds") or 10)
            heartbeat = asyncio.create_task(_heartbeat(transport, task, interval))
            await asyncio.wait({task})
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            return await self._finish(transport, events, self._outcome(task))
        finally:
            await transport.aclose()

    def _parse_config(self, raw: dict[str, Any]) -> Any:
        if self.config_model is None:
            return raw
        return self.config_model.model_validate(raw)

    def _outcome(self, task: asyncio.Task[Any]) -> dict[str, Any]:
        if task.cancelled():
            return {"outcome": "cancelled"}
        exc = task.exception()
        if exc is None:
            try:
                return {"outcome": "succeeded", "result": self._encode_result(task.result())}
            except Exception as encode_error:
                message = redact_text(f"result is invalid: {encode_error}")[:2000]
                return {
                    "outcome": "failed",
                    "error": {"code": "RESULT_INVALID", "message": message, "retryable": False},
                }
        _stderr(redact_text("".join(traceback.format_exception(exc))))
        if isinstance(exc, PlatformError):
            code, retryable, details = exc.code, exc.retryable, redact_value(exc.details)
        else:
            code, retryable, details = "AGENT_ERROR", False, {}
        message = redact_text(str(exc) or type(exc).__name__)[:2000]
        return {
            "outcome": "failed",
            "error": {"code": code, "message": message, "retryable": retryable, "details": details},
        }

    def _encode_result(self, value: Any) -> Any:
        if self.result_model is not None:
            model = value if isinstance(value, self.result_model) else self.result_model.model_validate(value)
            return model.model_dump(mode="json", by_alias=True)
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json", by_alias=True)
        return to_jsonable_python(value)

    async def _finish(self, transport: BrokerClient, events: EventsClient, body: dict[str, Any]) -> int:
        await events.aclose()
        try:
            response = await transport.request(
                "POST", "/result", operation="result", idempotent=True, json=body
            )
        except (PlatformError, Cancelled) as exc:
            _stderr(f"crewquarters: could not record the run outcome: {exc}")
            return EXIT_NO_OUTCOME
        state = response.get("runState") if isinstance(response, dict) else None
        return EXIT_SUCCEEDED if state == "SUCCEEDED" else EXIT_NOT_SUCCEEDED


async def _heartbeat(transport: BrokerClient, task: asyncio.Task[Any], interval: float) -> None:
    while not task.done():
        await asyncio.sleep(interval)
        try:
            response = await transport.request(
                "POST", "/heartbeat", operation="heartbeat", idempotent=True, json={}
            )
        except Cancelled:
            task.cancel()
            return
        except PlatformError as exc:
            _stderr(f"crewquarters: heartbeat failed: {exc}")
            continue
        if isinstance(response, dict) and response.get("cancelRequested"):
            task.cancel()
            return


def _install_signal_handlers(task: asyncio.Task[Any]) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError, RuntimeError, ValueError):
            loop.add_signal_handler(sig, task.cancel)
