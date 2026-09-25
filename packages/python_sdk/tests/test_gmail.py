from typing import Any

import httpx
from fakebroker import FakeBroker

from crewquarters._transport import BrokerClient
from crewquarters.google.gmail import GmailClient


def paged_broker(total: int, page_size: int = 100) -> tuple[FakeBroker, list[dict[str, Any]]]:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        start = int(params.get("pageToken", "0"))
        ids = [
            {"id": f"m{i}", "threadId": f"t{i}"}
            for i in range(start, min(total, start + page_size))
        ]
        body: dict[str, Any] = {"messages": ids, "resultSizeEstimate": total}
        if start + page_size < total:
            body["nextPageToken"] = str(start + page_size)
        return httpx.Response(200, json=body)

    broker = FakeBroker()
    broker.overrides[("GET", "/google/gmail/messages")] = handler
    return broker, seen


def gmail(broker: FakeBroker) -> GmailClient:
    return GmailClient(BrokerClient("http://broker.test", "t", http=broker.client()))


async def test_iter_crosses_pages_and_marks_truncation_at_the_limit() -> None:
    broker, seen = paged_broker(150)
    iteration = gmail(broker).iter_message_ids("after:1 before:2", limit=120)
    ids = [message_id async for message_id, _ in iteration]
    assert len(ids) == 120
    assert iteration.truncated is True
    assert [s.get("pageToken") for s in seen] == [None, "100"]
    assert seen[0]["q"] == "after:1 before:2"


async def test_iter_under_the_limit_is_not_truncated() -> None:
    broker, _ = paged_broker(150)
    iteration = gmail(broker).iter_message_ids("", limit=200)
    assert len([i async for i in iteration]) == 150
    assert iteration.truncated is False


async def test_iter_exactly_at_the_limit_is_not_truncated() -> None:
    broker, _ = paged_broker(100)
    iteration = gmail(broker).iter_message_ids("", limit=100)
    assert len([i async for i in iteration]) == 100
    assert iteration.truncated is False


async def test_list_passes_labels_and_page_token() -> None:
    broker, _ = paged_broker(5)
    page = await gmail(broker).list_message_ids(
        "q", max_results=2, page_token="0", label_ids=["INBOX", "Work"]
    )
    assert page.ids[:2] == [("m0", "t0"), ("m1", "t1")]
    assert page.result_size_estimate == 5
    assert broker.requests[-1].url.params.get_list("labelIds") == ["INBOX", "Work"]


async def test_get_message_parses_the_payload() -> None:
    broker = FakeBroker()
    broker.overrides[("GET", "/google/gmail/messages/m1")] = lambda r: httpx.Response(
        200,
        json={
            "id": "m1",
            "threadId": "t1",
            "snippet": "s",
            "payload": {
                "mimeType": "text/plain",
                "headers": [{"name": "Subject", "value": "Hi"}],
                "body": {"data": "SGk"},
            },
        },
    )
    message = await gmail(broker).get_message("m1")
    assert (message.subject, message.text_body) == ("Hi", "Hi")
