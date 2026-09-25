from datetime import UTC, datetime
from typing import Any

from crewquarters.errors import InvalidInput
from crewquarters.llm import ChatResult, Usage
from crewquarters.untrusted import parse_evidence
from gmail_digest.classify import DigestBatch, classify_batch
from gmail_digest.models import FetchedMessage


def fetched(i: int, text: str = "body") -> FetchedMessage:
    return FetchedMessage(
        id=f"id{i}",
        thread_id=f"t{i}",
        sender=f"Sender {i} <s{i}@example.com>",
        subject=f"Subject {i}",
        received_at=datetime(2026, 9, 23, 4, i, tzinfo=UTC),
        text=text,
        web_link=f"https://mail.google.com/mail/u/0/#all/t{i}",
    )


def result(parsed: DigestBatch) -> ChatResult:
    return ChatResult(
        text=parsed.model_dump_json(by_alias=True),
        structured=None,
        parsed=parsed,
        usage=Usage(),
        finish_reason="stop",
        provider="mock-local",
        model="mock-small",
        locality="local",
        latency_ms=1,
        request_id="r",
    )


class FakeLLM:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def chat(self, profile: str, messages: list[dict[str, str]], **kwargs: Any) -> ChatResult:
        self.calls.append({"profile": profile, "messages": messages, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response  # type: ignore[no-any-return]


class FakeEvents:
    def __init__(self) -> None:
        self.logs: list[str] = []

    async def log(self, level: str, message: str, **fields: Any) -> None:
        self.logs.append(message)


class Ctx:
    def __init__(self, llm: FakeLLM) -> None:
        self.llm = llm
        self.events = FakeEvents()


def batch(*items: dict[str, Any]) -> DigestBatch:
    return DigestBatch.model_validate({"items": list(items)})


def item(ref: str, priority: str = "low", **extra: Any) -> dict[str, Any]:
    return {
        "ref": ref,
        "priority": priority,
        "reason": f"reason {ref}",
        "nextAction": "none",
        **extra,
    }


async def test_items_map_back_to_message_ids_and_prompt_uses_evidence() -> None:
    llm = FakeLLM([result(batch(item("m1", "urgent"), item("m2", "important", uncertain=True)))])
    ctx = Ctx(llm)
    classified, chat = await classify_batch(
        ctx,
        [fetched(1, "The API is down"), fetched(2)],
        0,
        boundary="abcd",
        profile="local.general.small",
        tz_name="UTC",  # type: ignore[arg-type]
    )
    assert classified["id1"].priority == "urgent"
    assert classified["id2"].needs_review is True
    assert chat is not None and chat.locality == "local"
    call = llm.calls[0]
    assert call["temperature"] == 0
    assert call["response_model"] is DigestBatch
    assert call["idempotency_key"] == "digest-batch-0-a1-v1"
    system, user = call["messages"]
    assert system["role"] == "system" and "The API is down" not in system["content"]
    blocks = parse_evidence(user["content"])
    assert [b.ref for b in blocks] == ["m1", "m2"]
    assert "The API is down" in blocks[0].body
    assert "Subject: Subject 1" in blocks[0].body


async def test_unknown_refs_are_dropped_and_missing_messages_need_review() -> None:
    llm = FakeLLM([result(batch(item("m1", "low"), item("m9", "urgent")))])
    ctx = Ctx(llm)
    classified, _ = await classify_batch(
        ctx, [fetched(1), fetched(2)], 3, boundary="b", profile="p", tz_name="UTC"
    )  # type: ignore[arg-type]
    assert set(classified) == {"id1", "id2"}
    assert classified["id2"].priority == "important"
    assert classified["id2"].needs_review is True
    assert classified["id2"].reason == "Not classified automatically"
    assert any("m9" in log for log in ctx.events.logs)


async def test_invalid_output_gets_one_repair_retry() -> None:
    invalid = InvalidInput("bad", code="STRUCTURED_OUTPUT_INVALID", details={"raw": "oops"})
    llm = FakeLLM([invalid, result(batch(item("m1", "urgent")))])
    ctx = Ctx(llm)
    classified, _ = await classify_batch(
        ctx, [fetched(1)], 1, boundary="b", profile="p", tz_name="UTC"
    )  # type: ignore[arg-type]
    assert classified["id1"].priority == "urgent"
    assert len(llm.calls) == 2
    repair = llm.calls[1]["messages"]
    assert repair[-2] == {"role": "assistant", "content": "oops"}
    assert "did not match" in repair[-1]["content"]
    assert llm.calls[1]["idempotency_key"] == "digest-batch-1-a2-v1"


async def test_second_failure_marks_the_whole_batch_for_review() -> None:
    invalid = InvalidInput("bad", code="STRUCTURED_OUTPUT_INVALID", details={"raw": "oops"})
    llm = FakeLLM([invalid, invalid])
    ctx = Ctx(llm)
    classified, chat = await classify_batch(
        ctx, [fetched(1), fetched(2)], 0, boundary="b", profile="p", tz_name="UTC"
    )  # type: ignore[arg-type]
    assert chat is None
    assert {c.priority for c in classified.values()} == {"important"}
    assert all(c.needs_review for c in classified.values())


async def test_duplicate_refs_keep_the_first_answer() -> None:
    llm = FakeLLM([result(batch(item("m1", "urgent"), item("m1", "low")))])
    classified, _ = await classify_batch(
        Ctx(llm), [fetched(1)], 0, boundary="b", profile="p", tz_name="UTC"
    )  # type: ignore[arg-type]
    assert classified["id1"].priority == "urgent"
