import httpx
import pytest
from fakebroker import FakeBroker

from crewquarters._transport import BrokerClient
from crewquarters.events import EventsClient


def make(broker: FakeBroker) -> EventsClient:
    return EventsClient(BrokerClient("http://broker.test", "t", http=broker.client()), echo=None)


async def test_log_is_redacted_and_buffered_until_flush() -> None:
    broker = FakeBroker()
    events = make(broker)
    await events.log("info", "dialing +14155550123", to="+14155550123", attempt=1)
    assert broker.events == []
    await events.flush()
    [event] = broker.events
    assert event["type"] == "log"
    assert event["payload"] == {
        "level": "info",
        "message": "dialing ••••0123",
        "fields": {"to": "••••0123", "attempt": 1},
    }
    assert event["clientEventId"]
    assert event["occurredAt"].endswith("+00:00")


async def test_batch_flushes_automatically_at_fifty() -> None:
    broker = FakeBroker()
    events = make(broker)
    for i in range(50):
        await events.metric("n", i)
    assert broker.event_batches == [50]


async def test_progress_metric_and_artifact_payloads() -> None:
    broker = FakeBroker()
    events = make(broker)
    await events.progress(40, "Fetching", step="fetch")
    await events.metric("messages", 12, unit="count")
    await events.artifact("digest.json", "application/json", summary="3 groups", size_bytes=120)
    await events.flush()
    assert [e["payload"] for e in broker.events] == [
        {"percent": 40, "message": "Fetching", "step": "fetch"},
        {"name": "messages", "value": 12, "unit": "count"},
        {"name": "digest.json", "mediaType": "application/json", "summary": "3 groups", "sizeBytes": 120},
    ]


async def test_invalid_level_and_percent_are_rejected() -> None:
    events = make(FakeBroker())
    with pytest.raises(ValueError):
        await events.log("loud", "x")
    with pytest.raises(ValueError):
        await events.progress(101, "x")


async def test_flush_failure_keeps_events_and_does_not_raise_from_add() -> None:
    broker = FakeBroker()
    broker.overrides[("POST", "/events")] = lambda request: httpx.Response(
        409, json={"error": {"code": "RUN_NOT_ACTIVE", "message": "gone"}}
    )
    events = make(broker)
    for i in range(50):
        await events.metric("n", i)
    del broker.overrides[("POST", "/events")]
    await events.aclose()
    assert len(broker.events) == 50


async def test_background_flusher_sends_events() -> None:
    broker = FakeBroker()
    events = EventsClient(
        BrokerClient("http://broker.test", "t", http=broker.client()), flush_interval=0.01, echo=None
    )
    events.start()
    await events.log("info", "hello")
    for _ in range(100):
        if broker.events:
            break
        import asyncio

        await asyncio.sleep(0.01)
    await events.aclose()
    assert len(broker.events) == 1
