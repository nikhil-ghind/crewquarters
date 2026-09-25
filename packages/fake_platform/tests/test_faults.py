import time

import pytest

from crewquarters_fake.errors import ApiError
from crewquarters_fake.faults import FaultRegistry


class Op:
    def __init__(self) -> None:
        self.applied = 0

    async def __call__(self) -> str:
        self.applied += 1
        return "ok"


async def test_no_fault_runs_the_operation() -> None:
    op = Op()
    assert await FaultRegistry().run("broker.sheets.update", op) == "ok"
    assert op.applied == 1


async def test_error_fault_does_not_apply_and_is_consumed() -> None:
    registry = FaultRegistry()
    registry.add("broker.sheets.update", "error", count=2, status=503, code="PROVIDER_UNAVAILABLE")
    op = Op()
    for _ in range(2):
        with pytest.raises(ApiError) as info:
            await registry.run("broker.sheets.update", op)
        assert (info.value.status, info.value.code) == (503, "PROVIDER_UNAVAILABLE")
    assert op.applied == 0
    assert await registry.run("broker.sheets.update", op) == "ok"


async def test_apply_then_drop_applies_then_times_out() -> None:
    registry = FaultRegistry()
    registry.add("broker.telephony.create", "apply-then-drop")
    op = Op()
    with pytest.raises(ApiError) as info:
        await registry.run("broker.telephony.create", op)
    assert (info.value.status, info.value.code) == (504, "TIMEOUT")
    assert op.applied == 1


async def test_delay_fault_sleeps_then_applies() -> None:
    registry = FaultRegistry()
    registry.add("broker.llm.chat", "delay", delay_ms=50)
    op = Op()
    started = time.monotonic()
    assert await registry.run("broker.llm.chat", op) == "ok"
    assert time.monotonic() - started >= 0.045


async def test_faults_only_match_their_target_and_clear() -> None:
    registry = FaultRegistry()
    registry.add("broker.sheets.append", "error")
    op = Op()
    assert await registry.run("broker.sheets.update", op) == "ok"
    registry.clear()
    assert await registry.run("broker.sheets.append", op) == "ok"


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError):
        FaultRegistry().add("x", "explode")
