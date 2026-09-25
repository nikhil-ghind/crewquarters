import json

import httpx
import pytest
from fakebroker import FakeBroker

from crewquarters._transport import BrokerClient
from crewquarters.errors import OutcomeUnknown, ProviderError
from crewquarters.google.sheets import SheetsClient


def sheets(broker: FakeBroker) -> SheetsClient:
    async def no_sleep(_: float) -> None:
        return None

    return SheetsClient(
        BrokerClient("http://broker.test", "t", http=broker.client(), sleep=no_sleep)
    )


async def test_get_update_append_round_trip() -> None:
    broker = FakeBroker()
    bodies = []

    def record(response: dict) -> object:  # type: ignore[type-arg]
        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json=response)

        return handler

    broker.overrides[("POST", "/google/sheets/values:get")] = record(
        {"range": "A", "values": [["a"]]}
    )  # type: ignore[assignment]
    broker.overrides[("POST", "/google/sheets/values:update")] = record(  # type: ignore[assignment]
        {"updatedRange": "Results!A2:B2", "updatedRows": 1}
    )
    broker.overrides[("POST", "/google/sheets/values:append")] = record(  # type: ignore[assignment]
        {"updatedRange": "Results!A3:B3", "updatedRows": 1}
    )
    client = sheets(broker)
    assert await client.get_values("s1", "Contacts!A2:D") == [["a"]]
    updated = await client.update_values("s1", "Results!A2:B2", [["2", "Asha"]])
    appended = await client.append_values("s1", "Results!A:B", [["3", "Ben"]])
    assert (updated.updated_range, updated.updated_rows) == ("Results!A2:B2", 1)
    assert appended.updated_range == "Results!A3:B3"
    assert bodies[1] == {"spreadsheetId": "s1", "range": "Results!A2:B2", "values": [["2", "Asha"]]}


async def test_get_values_without_values_key_is_empty() -> None:
    broker = FakeBroker()
    broker.overrides[("POST", "/google/sheets/values:get")] = lambda r: httpx.Response(
        200, json={"range": "A"}
    )
    assert await sheets(broker).get_values("s1", "A") == []


async def test_update_is_retried_but_append_is_not() -> None:
    calls = {"update": 0, "append": 0}

    def failing(kind: str) -> object:
        def handler(request: httpx.Request) -> httpx.Response:
            calls[kind] += 1
            return httpx.Response(504, json={"error": {"code": "TIMEOUT", "message": "t"}})

        return handler

    broker = FakeBroker()
    broker.overrides[("POST", "/google/sheets/values:update")] = failing("update")  # type: ignore[assignment]
    broker.overrides[("POST", "/google/sheets/values:append")] = failing("append")  # type: ignore[assignment]
    client = sheets(broker)
    with pytest.raises(ProviderError):
        await client.update_values("s1", "R!A1", [["x"]])
    with pytest.raises(OutcomeUnknown):
        await client.append_values("s1", "R!A:B", [["x"]])
    assert calls == {"update": 4, "append": 1}
