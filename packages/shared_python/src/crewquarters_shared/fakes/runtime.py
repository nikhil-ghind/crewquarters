"""In-process fake runtime for laptops and tests.

It simulates an agent container by calling the run service directly, following the
same protocol a real SDK uses through the broker: handshake, heartbeats, events,
optional input request, result. The installation config key ``fakeScenario``
selects behavior: ``succeed`` (default), ``ask``, ``fail``, ``hang``, ``crash`` (exits 1
before the handshake), ``oom`` (killed for memory after the handshake, exit 137),
``exit0`` (exits 0 after the handshake without a result), ``slow``, ``model``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import platform
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_shared.db.models import InputRequest
from crewquarters_shared.errors import PlatformError
from crewquarters_shared.runs import service
from crewquarters_shared.runtime import RunSpec, RuntimeStatus

log = logging.getLogger(__name__)
T = TypeVar("T")


class FakeRuntime:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        heartbeat_seconds: int = 30,
        step_seconds: float = 0.2,
    ) -> None:
        self._sessions = sessions
        self._heartbeat_seconds = heartbeat_seconds
        self._step_seconds = step_seconds
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._status: dict[str, RuntimeStatus] = {}
        self.started: list[str] = []
        # Tests: installation id -> scenario, for scenarios that the hello-crew manifest's
        # config schema does not offer (``oom``, ``exit0``).
        self.forced_scenarios: dict[str, str] = {}

    async def start_run(self, spec: RunSpec) -> str:
        ref = f"fake-{spec.run_id}-{spec.attempt}"
        if ref not in self._tasks:
            self.started.append(ref)
            self._status[ref] = RuntimeStatus("starting")
            self._tasks[ref] = asyncio.create_task(self._simulate(spec, ref), name=ref)
        return ref

    async def get_run(self, runtime_ref: str) -> RuntimeStatus:
        return self._status.get(runtime_ref, RuntimeStatus("missing"))

    async def stop_run(self, runtime_ref: str, grace_seconds: int) -> None:
        task = self._tasks.get(runtime_ref)
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if runtime_ref in self._status:
            self._status[runtime_ref] = RuntimeStatus("exited", 143)

    async def capacity(self) -> dict[str, Any]:
        return {
            "runtime": "fake",
            "architecture": platform.machine(),
            "gpu": {"available": False},
            "docker": {"available": False},
        }

    async def close(self) -> None:
        for ref in list(self._tasks):
            await self.stop_run(ref, 0)

    async def _call(self, fn: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
        async with self._sessions() as session, session.begin():
            return await fn(session, *args, **kwargs)

    async def _simulate(self, spec: RunSpec, ref: str) -> None:
        scenario = self.forced_scenarios.get(
            str(spec.installation_id), str(spec.config.get("fakeScenario", "succeed"))
        )
        step = float(spec.config.get("fakeStepSeconds", self._step_seconds))
        run_id, attempt = spec.run_id, spec.attempt
        heartbeat_task: asyncio.Task[None] | None = None
        try:
            if scenario == "crash":
                self._status[ref] = RuntimeStatus("exited", 1)
                return
            memory = spec.memory_mb * 1024 * 1024
            if scenario == "hang":
                self._status[ref] = RuntimeStatus("running")
                await asyncio.sleep(3600)
                return
            await self._call(service.handshake, run_id, attempt, self._heartbeat_seconds)
            self._status[ref] = RuntimeStatus("running")
            heartbeat_task = asyncio.create_task(self._heartbeats(run_id, attempt, ref))
            await self._progress(run_id, attempt, 10, "Starting")
            if scenario == "oom":
                self._status[ref] = RuntimeStatus(
                    "exited", 137, oom_killed=True, memory_limit_bytes=memory
                )
                return
            if scenario == "exit0":
                self._status[ref] = RuntimeStatus("exited", 0, memory_limit_bytes=memory)
                return
            if scenario == "model":
                await self._call(
                    service.set_model_loading, run_id, attempt, True, "local.general.small"
                )
                await asyncio.sleep(step)
                await self._call(service.set_model_loading, run_id, attempt, False, None)
            await asyncio.sleep(step)
            await self._progress(run_id, attempt, 50, "Working")
            outcome: dict[str, Any] = {"message": "Hello from the fake runtime."}
            if scenario == "ask":
                answer = await self._ask(run_id, attempt, step)
                outcome["decision"] = answer
            if scenario == "slow":
                await asyncio.sleep(3600)
            await asyncio.sleep(step)
            await self._progress(run_id, attempt, 100, "Complete")
            if scenario == "fail":
                await self._call(
                    service.finish,
                    run_id,
                    attempt,
                    status="failed",
                    result=None,
                    error={
                        "code": "FAKE_FAILURE",
                        "message": "Simulated failure.",
                        "retryable": True,
                    },
                )
                self._status[ref] = RuntimeStatus("exited", 1)
                return
            await self._call(
                service.finish, run_id, attempt, status="succeeded", result=outcome, error=None
            )
            self._status[ref] = RuntimeStatus("exited", 0)
        except asyncio.CancelledError:
            self._status[ref] = RuntimeStatus("exited", 143)
            raise
        except PlatformError as exc:
            log.info("fake agent %s stopped: %s", ref, exc.code)
            self._status[ref] = RuntimeStatus("exited", 1)
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()

    async def _progress(self, run_id: Any, attempt: int, percent: int, message: str) -> None:
        await self._call(
            service.record_agent_event,
            run_id,
            attempt,
            "run.progress",
            {"percent": percent, "message": message},
        )

    async def _ask(self, run_id: Any, attempt: int, step: float) -> Any:
        request = await self._call(
            service.ask,
            run_id,
            attempt,
            key="confirm",
            title="Continue?",
            prompt="The fake agent is ready to act. Should it continue?",
            schema={
                "type": "object",
                "required": ["decision"],
                "properties": {"decision": {"type": "string", "enum": ["continue", "cancel"]}},
            },
            timeout_seconds=3600,
        )
        request_id = request.id
        while True:
            async with self._sessions() as session:
                current = await session.get(InputRequest, request_id)
            assert current is not None
            if current.state == "answered":
                return (current.answer or {}).get("decision")
            if current.state in ("cancelled", "expired"):
                return None
            await asyncio.sleep(step)

    async def _heartbeats(self, run_id: Any, attempt: int, ref: str) -> None:
        interval = max(0.05, self._heartbeat_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                state = await self._call(
                    service.heartbeat, run_id, attempt, self._heartbeat_seconds
                )
            except PlatformError:
                return
            if state.get("cancelRequested"):
                task = self._tasks.get(ref)
                if task:
                    task.cancel()
                return
