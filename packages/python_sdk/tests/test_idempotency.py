from typing import Any

import pytest
from fakebroker import FakeBroker
from pydantic import BaseModel

from crewquarters._transport import BrokerClient
from crewquarters.errors import OutcomeUnknown
from crewquarters.idempotency import IdempotencyClient


def make(broker: FakeBroker) -> IdempotencyClient:
    return IdempotencyClient(BrokerClient("http://broker.test", "t", http=broker.client()))


class Counter:
    def __init__(self, value: Any) -> None:
        self.calls = 0
        self.value = value

    async def __call__(self) -> Any:
        self.calls += 1
        return self.value


async def test_first_claim_runs_fn_and_completes() -> None:
    broker = FakeBroker()
    fn = Counter({"sid": "CA1"})
    assert await make(broker).once("call:1", fn) == {"sid": "CA1"}
    assert fn.calls == 1
    assert broker.idempotency["call:1"]["state"] == "completed"
    assert broker.idempotency["call:1"]["result"] == {"sid": "CA1"}


async def test_completed_key_returns_stored_result_without_calling() -> None:
    broker = FakeBroker()
    client = make(broker)
    await client.once("k", Counter(7))
    fn = Counter(8)
    assert await client.once("k", fn) == 7
    assert fn.calls == 0


async def test_in_progress_from_earlier_attempt_raises_outcome_unknown() -> None:
    broker = FakeBroker()
    await make(broker).claim("k")
    broker.attempt = 2
    fn = Counter(1)
    with pytest.raises(OutcomeUnknown):
        await make(broker).once("k", fn)
    assert fn.calls == 0


async def test_resume_in_progress_takes_over_and_runs() -> None:
    broker = FakeBroker()
    await make(broker).claim("k")
    broker.attempt = 2
    fn = Counter("done")
    assert await make(broker).once("k", fn, resume_in_progress=True) == "done"
    assert fn.calls == 1
    assert broker.idempotency["k"]["claimedByAttempt"] == 2


class CallResult(BaseModel):
    sid: str
    minutes: int


async def test_result_type_round_trips_the_same_type() -> None:
    broker = FakeBroker()
    client = make(broker)
    first = await client.once("k", Counter(CallResult(sid="CA1", minutes=2)), result_type=CallResult)
    replay = await client.once("k", Counter(None), result_type=CallResult)
    assert first == replay == CallResult(sid="CA1", minutes=2)
    assert broker.idempotency["k"]["result"] == {"sid": "CA1", "minutes": 2}


async def test_get_returns_none_for_unknown_key() -> None:
    assert await make(FakeBroker()).get("missing") is None
