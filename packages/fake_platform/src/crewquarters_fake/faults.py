"""Fault injection for broker operations (spec section 6.2)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from crewquarters_fake.errors import ApiError

T = TypeVar("T")
MODES = frozenset({"error", "delay", "apply-then-drop"})


@dataclass
class Fault:
    target: str
    mode: str
    count: int
    status: int | None = None
    code: str | None = None
    delay_ms: int = 0


class FaultRegistry:
    def __init__(self) -> None:
        self._faults: list[Fault] = []

    def add(
        self,
        target: str,
        mode: str,
        count: int = 1,
        status: int | None = None,
        code: str | None = None,
        delay_ms: int = 0,
    ) -> Fault:
        if mode not in MODES:
            raise ValueError(f"fault mode must be one of {sorted(MODES)}")
        fault = Fault(target, mode, max(1, count), status, code, delay_ms)
        self._faults.append(fault)
        return fault

    def clear(self) -> None:
        self._faults.clear()

    def list(self) -> list[Fault]:
        return list(self._faults)

    def take(self, target: str) -> Fault | None:
        for fault in self._faults:
            if fault.target == target:
                fault.count -= 1
                if fault.count <= 0:
                    self._faults.remove(fault)
                return fault
        return None

    async def run(self, target: str, operation: Callable[[], Awaitable[T]]) -> T:
        fault = self.take(target)
        if fault is None:
            return await operation()
        if fault.mode == "error":
            status = fault.status or 503
            code = fault.code or ("PROVIDER_UNAVAILABLE" if status >= 500 else "INJECTED_FAULT")
            raise ApiError(status, code, f"injected fault on {target}")
        if fault.mode == "delay":
            await asyncio.sleep(fault.delay_ms / 1000)
            return await operation()
        await operation()
        raise ApiError(504, "TIMEOUT", f"injected timeout after applying {target}")
