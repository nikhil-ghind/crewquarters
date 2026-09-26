import json

import httpx
from fakebroker import FakeBroker

from crewquarters._transport import BrokerClient
from crewquarters.knowledge import KnowledgeClient
from crewquarters.untrusted import parse_evidence

PASSAGE = {
    "citationId": "kb:kb-1:doc:terms:chunk:0",
    "text": "Refunds within 30 days.",
    "score": 1.5,
    "document": {"id": "terms", "name": "terms.md"},
    "locator": {"section": "Refunds"},
}


async def test_search_sends_request_and_wraps_passages_as_evidence() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"passages": [PASSAGE]})

    broker = FakeBroker()
    broker.overrides[("POST", "/knowledge/search")] = handler
    knowledge = KnowledgeClient(BrokerClient("http://broker.test", "t", http=broker.client()))
    result = await knowledge.search(
        "kb-1", "refund?", top_k=3, document_ids=["terms"], max_context_tokens=500
    )
    assert seen[0] == {
        "knowledgeBaseId": "kb-1",
        "query": "refund?",
        "topK": 3,
        "filters": {"documentIds": ["terms"]},
        "maxContextTokens": 500,
    }
    [passage] = result.passages
    assert (passage.citation_id, passage.document_name, passage.locator) == (
        "kb:kb-1:doc:terms:chunk:0",
        "terms.md",
        {"section": "Refunds"},
    )
    [block] = parse_evidence(result.as_context())
    assert block.ref == "kb:kb-1:doc:terms:chunk:0"
    assert block.body == "Refunds within 30 days."
    assert "terms.md" in block.source


DOCS = {
    "documents": [
        {"id": "a", "name": "policy-2025.md", "mime": "text/markdown", "bytes": 10},
        {"id": "b", "name": "policy-2026.md", "mime": "text/markdown", "bytes": 20},
        {"id": "c", "name": "policy-notes.txt", "mime": "text/plain", "bytes": None},
    ],
    "total": 3,
    "truncated": False,
}


def client_with(broker: FakeBroker, granted: tuple[str, ...] = ("kb-1",)) -> KnowledgeClient:
    return KnowledgeClient(BrokerClient("http://broker.test", "t", http=broker.client()), granted)


async def test_find_files_sends_the_glob_and_returns_files() -> None:
    broker = FakeBroker()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=DOCS)

    broker.overrides[("GET", "/knowledge/documents")] = handler
    matches = await client_with(broker).connect().find_files("policy-*", limit=10)
    assert dict(seen[0].url.params) == {
        "knowledgeBaseId": "kb-1",
        "pattern": "policy-*",
        "limit": "10",
    }
    assert [f.name for f in matches.files] == [
        "policy-2025.md",
        "policy-2026.md",
        "policy-notes.txt",
    ]
    assert matches.ids() == ["a", "b", "c"] and matches.files[2].bytes is None
    assert matches.total == 3 and matches.truncated is False


async def test_find_files_can_also_filter_by_regex_locally() -> None:
    broker = FakeBroker()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=DOCS)

    broker.overrides[("GET", "/knowledge/documents")] = handler
    matches = await client_with(broker).find_files("kb-1", "*.md", regex=r"-20\d\d\.md$")
    assert [f.name for f in matches.files] == ["policy-2025.md", "policy-2026.md"]
    assert matches.total == 2
    assert seen[0].url.params["limit"] == "200"  # the regex needs every glob match


async def test_a_bad_regex_is_rejected_before_any_request() -> None:
    from crewquarters.errors import InvalidInput

    broker = FakeBroker()
    try:
        await client_with(broker).find_files("kb-1", regex="(")
    except InvalidInput as exc:
        assert "regular expression" in str(exc)
    else:
        raise AssertionError("expected InvalidInput")


async def test_search_in_files_limits_the_search_to_the_matching_documents() -> None:
    broker = FakeBroker()
    bodies: list[dict[str, object]] = []
    broker.overrides[("GET", "/knowledge/documents")] = lambda r: httpx.Response(200, json=DOCS)

    def search(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"passages": [PASSAGE]})

    broker.overrides[("POST", "/knowledge/search")] = search
    kb = client_with(broker).connect()
    result = await kb.search("refunds", files="policy-*.md")
    assert bodies[0]["filters"] == {"documentIds": ["a", "b", "c"]}
    assert bodies[0]["knowledgeBaseId"] == "kb-1"
    assert result.passages[0].document_name == "terms.md"


async def test_search_in_files_with_no_match_returns_nothing_without_searching() -> None:
    broker = FakeBroker()
    broker.overrides[("GET", "/knowledge/documents")] = lambda r: httpx.Response(
        200, json={"documents": [], "total": 0, "truncated": False}
    )
    result = await client_with(broker).connect().search("refunds", files="nothing-*")
    assert result.passages == []


def test_connect_needs_exactly_one_granted_base_unless_told_which() -> None:
    from crewquarters.errors import InvalidInput

    broker = FakeBroker()
    assert client_with(broker).connect().id == "kb-1"
    assert client_with(broker, ()).connect("kb-9").id == "kb-9"
    for granted in [(), ("a", "b")]:
        try:
            client_with(broker, granted).connect()
        except InvalidInput as exc:
            assert exc.code == "KNOWLEDGE_BASE_UNAVAILABLE"
        else:
            raise AssertionError("expected InvalidInput")
