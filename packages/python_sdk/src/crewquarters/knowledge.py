"""Knowledge-base search scoped to the knowledge bases bound to the installation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crewquarters._transport import BrokerClient
from crewquarters.untrusted import evidence, new_boundary


@dataclass(frozen=True)
class Passage:
    citation_id: str
    text: str
    score: float
    document_id: str
    document_name: str
    locator: dict[str, Any]


@dataclass(frozen=True)
class SearchResult:
    passages: list[Passage]

    def as_context(self, boundary: str | None = None) -> str:
        """Passages wrapped as untrusted evidence blocks, labelled with their citation ids."""
        boundary = boundary or new_boundary()
        blocks = []
        for passage in self.passages:
            section = passage.locator.get("section")
            source = f"{passage.document_name} § {section}" if section else passage.document_name
            blocks.append(evidence(passage.text, ref=passage.citation_id, source=source, boundary=boundary))
        return "\n\n".join(blocks)


class KnowledgeClient:
    def __init__(self, transport: BrokerClient) -> None:
        self._transport = transport

    async def search(
        self,
        knowledge_base_id: str,
        query: str,
        *,
        top_k: int = 8,
        document_ids: list[str] | None = None,
        max_context_tokens: int | None = None,
    ) -> SearchResult:
        body: dict[str, Any] = {
            "knowledgeBaseId": knowledge_base_id,
            "query": query,
            "topK": top_k,
            "filters": {"documentIds": document_ids or []},
        }
        if max_context_tokens is not None:
            body["maxContextTokens"] = max_context_tokens
        data = await self._transport.request(
            "POST", "/knowledge/search", operation="knowledge.search", idempotent=True, json=body
        )
        return SearchResult(
            [
                Passage(
                    citation_id=str(p["citationId"]),
                    text=str(p["text"]),
                    score=float(p["score"]),
                    document_id=str(p["document"]["id"]),
                    document_name=str(p["document"]["name"]),
                    locator=dict(p.get("locator", {})),
                )
                for p in data.get("passages", [])
            ]
        )
