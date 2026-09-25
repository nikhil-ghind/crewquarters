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
    assert event["type"] == "run.log"
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
    assert [(e["type"], e["payload"]) for e in broker.events] == [
        ("run.progress", {"percent": 40, "message": "Fetching", "step": "fetch"}),
        ("run.metric", {"name": "messages", "value": 12, "unit": "count"}),
        (
            "run.artifact",
            {
                "name": "digest.json",
                "mediaType": "application/json",
                "summary": "3 groups",
                "bytes": 120,
            },
        ),
    ]


async def test_absent_optional_fields_are_omitted_not_null() -> None:
    broker = FakeBroker()
    events = make(broker)
    await events.progress(None, "Working")
    await events.metric("n", 1)
    await events.artifact("report")
    await events.flush()
    assert [e["payload"] for e in broker.events] == [
        {"percent": None, "message": "Working"},
        {"name": "n", "value": 1},
        {"name": "report"},
    ]


async def test_invalid_level_and_percent_are_rejected() -> None:
    events = make(FakeBroker())
    with pytest.raises(ValueError):
        await events.log("loud", "x")
    with pytest.raises(ValueError):
        await events.progress(101, "x")


async def test_retryable_flush_failure_keeps_events_and_does_not_raise_from_add() -> None:
    async def no_sleep(_: float) -> None:
        return None

    broker = FakeBroker()
    broker.overrides[("POST", "/events")] = lambda request: httpx.Response(
        503, json={"error": {"code": "PROVIDER_UNAVAILABLE", "message": "broker restarting"}}
    )
    events = EventsClient(
        BrokerClient("http://broker.test", "t", http=broker.client(), sleep=no_sleep), echo=None
    )
    for i in range(50):
        await events.metric("n", i)
    del broker.overrides[("POST", "/events")]
    await events.aclose()
    assert len(broker.events) == 50


async def test_background_flusher_sends_events() -> None:
    broker = FakeBroker()
    events = EventsClient(
        BrokerClient("http://broker.test", "t", http=broker.client()),
        flush_interval=0.01,
        echo=None,
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


async def test_non_json_native_fields_are_serialized() -> None:
    from datetime import UTC, datetime

    class Opaque:
        def __str__(self) -> str:
            return "opaque-thing"

    broker = FakeBroker()
    events = make(broker)
    await events.log(
        "info", "started", when=datetime(2026, 9, 24, 10, 0, tzinfo=UTC), tags={"a"}, obj=Opaque()
    )
    await events.flush()
    assert broker.events[0]["payload"]["fields"] == {
        "when": "2026-09-24T10:00:00Z",
        "tags": ["a"],
        "obj": "opaque-thing",
    }


@pytest.mark.parametrize(
    ("value", "size"), [("12", None), (float("nan"), None), (float("inf"), None), (True, None)]
)
async def test_invalid_metric_values_are_rejected_at_the_call(value: object, size: object) -> None:
    events = make(FakeBroker())
    with pytest.raises(ValueError):
        await events.metric("count", value)  # type: ignore[arg-type]


async def test_negative_artifact_size_is_rejected_at_the_call() -> None:
    with pytest.raises(ValueError):
        await make(FakeBroker()).artifact("x", "text/plain", size_bytes=-1)


async def test_rejected_batch_is_salvaged_event_by_event() -> None:
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        batch = json.loads(request.content)["events"]
        if any(e["payload"].get("message") == "bad" for e in batch):
            return httpx.Response(
                422, json={"error": {"code": "INVALID_REQUEST", "message": "invalid event"}}
            )
        broker.events.extend(batch)
        return httpx.Response(
            200, json={"accepted": len(batch), "lastSequence": len(broker.events)}
        )

    broker = FakeBroker()
    broker.overrides[("POST", "/events")] = handler
    notes: list[str] = []
    events = EventsClient(
        BrokerClient("http://broker.test", "t", http=broker.client()), echo=notes.append
    )
    for message in ("ok1", "bad", "ok2"):
        await events.log("info", message)
    await events.flush()
    await events.log("info", "ok3")
    await events.aclose()
    assert [e["payload"]["message"] for e in broker.events] == ["ok1", "ok2", "ok3"]
    assert any("dropped 1 event" in note for note in notes)
