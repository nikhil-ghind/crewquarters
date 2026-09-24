import pytest
from fakebroker import FakeBroker, answered

from crewquarters._transport import BrokerClient
from crewquarters.context import Limits
from crewquarters.errors import Cancelled, InputTimeout, InvalidInput
from crewquarters.input import Choice, InputClient, key_value_block, table_block, text_block


def make(broker: FakeBroker, remaining: float = 3600) -> InputClient:
    transport = BrokerClient("http://broker.test", "t", http=broker.client())
    return InputClient(transport, Limits(active_timeout_seconds=600, input_wait_remaining_seconds=remaining))


async def test_ask_with_choices_builds_schema_and_returns_value() -> None:
    broker = FakeBroker()
    broker.input_script["confirm-v1"] = [
        {"state": "pending"},
        {"state": "pending"},
        answered({"choice": "go"}),
    ]
    answer = await make(broker).ask(
        "confirm-v1",
        "Continue?",
        "Should the agent continue?",
        choices=[Choice("go", "Approve 3 calls", "primary"), "stop"],
        preview=[text_block("Script text"), key_value_block([("Recipients", "3")])],
        consequence="3 calls will be placed.",
        timeout_seconds=600,
    )
    assert answer.value == "go"
    assert answer.data == {"choice": "go"}
    assert answer.answered_by == "owner"
    [create] = broker.input_creates
    assert create["schema"] == {
        "type": "object",
        "required": ["choice"],
        "properties": {"choice": {"type": "string", "enum": ["go", "stop"]}},
    }
    assert create["choices"] == [
        {"value": "go", "label": "Approve 3 calls", "style": "primary"},
        {"value": "stop", "label": "stop", "style": "secondary"},
    ]
    assert create["preview"][1] == {"type": "keyValue", "items": [{"label": "Recipients", "value": "3"}]}
    assert create["consequence"] == "3 calls will be placed."
    assert create["timeoutSeconds"] == 600
    polls = [r for r in broker.requests if r.method == "GET"]
    assert polls and all(r.url.params["waitSeconds"] == "25" for r in polls)


async def test_first_string_choice_is_primary() -> None:
    broker = FakeBroker()
    broker.input_script["k"] = [answered({"choice": "a"})]
    await make(broker).ask("k", "t", "p", choices=["a", "b"], timeout_seconds=60)
    assert [c["style"] for c in broker.input_creates[0]["choices"]] == ["primary", "secondary"]


async def test_ask_with_schema_returns_data_as_value() -> None:
    broker = FakeBroker()
    broker.input_script["k"] = [answered({"count": 2})]
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
    answer = await make(broker).ask("k", "t", "p", schema=schema, timeout_seconds=60)
    assert answer.value == {"count": 2}


async def test_expired_raises_input_timeout() -> None:
    broker = FakeBroker()
    broker.input_script["k"] = [{"state": "expired"}]
    with pytest.raises(InputTimeout):
        await make(broker).ask("k", "t", "p", choices=["a"], timeout_seconds=60)


async def test_cancelled_request_raises_cancelled() -> None:
    broker = FakeBroker()
    broker.input_script["k"] = [{"state": "pending"}, {"state": "cancelled"}]
    with pytest.raises(Cancelled):
        await make(broker).ask("k", "t", "p", choices=["a"], timeout_seconds=60)


async def test_timeout_above_budget_is_rejected_before_any_request() -> None:
    broker = FakeBroker()
    with pytest.raises(InvalidInput) as info:
        await make(broker, remaining=100).ask("k", "t", "p", choices=["a"], timeout_seconds=101)
    assert info.value.code == "INPUT_WAIT_BUDGET_EXCEEDED"
    assert broker.requests == []


async def test_exactly_one_of_schema_or_choices() -> None:
    client = make(FakeBroker())
    with pytest.raises(InvalidInput):
        await client.ask("k", "t", "p", timeout_seconds=60)
    with pytest.raises(InvalidInput):
        await client.ask("k", "t", "p", schema={"type": "object"}, choices=["a"], timeout_seconds=60)


def test_table_block_stringifies_cells() -> None:
    assert table_block(["Row", "Name"], [[2, "Asha"]]) == {
        "type": "table",
        "columns": ["Row", "Name"],
        "rows": [["2", "Asha"]],
    }
