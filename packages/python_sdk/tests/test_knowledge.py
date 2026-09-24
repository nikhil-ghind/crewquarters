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
