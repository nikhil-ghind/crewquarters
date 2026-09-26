"""Knowledge-base search scoped to the knowledge bases bound to the installation."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from crewquarters._transport import BrokerClient
from crewquarters.errors import InvalidInput
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
            blocks.append(
                evidence(passage.text, ref=passage.citation_id, source=source, boundary=boundary)
            )
        return "\n\n".join(blocks)


@dataclass(frozen=True)
class KnowledgeFile:
    id: str
    name: str
    mime: str | None
    bytes: int | None


@dataclass(frozen=True)
class FileMatches:
    """Documents found by name. ``total`` counts every match; ``truncated`` is true when
    ``files`` holds fewer than that."""

    files: list[KnowledgeFile]
    total: int
    truncated: bool

    def ids(self) -> list[str]:
        return [f.id for f in self.files]


MAX_LISTED = 200


class KnowledgeClient:
    def __init__(self, transport: BrokerClient, granted: Sequence[str] = ()) -> None:
        self._transport = transport
        self._granted = tuple(granted)

    def connect(self, knowledge_base_id: str | None = None) -> KnowledgeBase:
        """A handle on one knowledge base. Without an id it is the base the owner selected for
        this installation. Only that base is reachable: the broker refuses any other."""
        if knowledge_base_id is None:
            if len(self._granted) != 1:
                raise InvalidInput(
                    "no knowledge base is selected for this installation"
                    if not self._granted
                    else "several knowledge bases are granted; pass knowledge_base_id",
                    code="KNOWLEDGE_BASE_UNAVAILABLE",
                )
            knowledge_base_id = self._granted[0]
        return KnowledgeBase(self, knowledge_base_id)

    async def find_files(
        self,
        knowledge_base_id: str,
        pattern: str = "*",
        *,
        regex: str | None = None,
        limit: int = 50,
    ) -> FileMatches:
        """Ready documents whose file name matches ``pattern`` (a case-insensitive glob: ``*``,
        ``?``, ``[abc]``) and, when given, ``regex`` (matched anywhere in the name, case-sensitive
        unless you add ``(?i)``). The regex is applied here, so with a regex at most the first
        ``MAX_LISTED`` glob matches are considered and ``truncated`` says when more existed."""
        pattern_regex = _compile(regex)
        data = await self._transport.request(
            "GET",
            "/knowledge/documents",
            operation="knowledge.documents",
            idempotent=True,
            params={
                "knowledgeBaseId": knowledge_base_id,
                "pattern": pattern,
                "limit": MAX_LISTED if pattern_regex else max(1, min(limit, MAX_LISTED)),
            },
        )
        files = [
            KnowledgeFile(str(d["id"]), str(d["name"]), d.get("mime"), _int_or_none(d.get("bytes")))
            for d in data.get("documents", [])
        ]
        total, truncated = int(data.get("total", len(files))), bool(data.get("truncated"))
        if pattern_regex:
            files = [f for f in files if pattern_regex.search(f.name)]
            total = len(files)
        return FileMatches(files[: max(1, limit)], total, truncated or total > max(1, limit))

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


class KnowledgeBase:
    """One connected knowledge base: find files by name pattern, then search all or some of them."""

    def __init__(self, client: KnowledgeClient, knowledge_base_id: str) -> None:
        self._client = client
        self.id = knowledge_base_id

    async def find_files(
        self, pattern: str = "*", *, regex: str | None = None, limit: int = 50
    ) -> FileMatches:
        return await self._client.find_files(self.id, pattern, regex=regex, limit=limit)

    async def search(
        self,
        query: str,
        *,
        files: str | None = None,
        top_k: int = 8,
        max_context_tokens: int | None = None,
    ) -> SearchResult:
        """Search the base. ``files`` limits the search to documents whose name matches that glob
        (for example ``"policy-*.md"``); no match means no passages."""
        document_ids: list[str] | None = None
        if files is not None:
            found = await self.find_files(files, limit=MAX_LISTED)
            if not found.files:
                return SearchResult([])
            document_ids = found.ids()
        return await self._client.search(
            self.id,
            query,
            top_k=top_k,
            document_ids=document_ids,
            max_context_tokens=max_context_tokens,
        )


def _compile(regex: str | None) -> re.Pattern[str] | None:
    if regex is None:
        return None
    try:
        return re.compile(regex)
    except re.error as exc:
        raise InvalidInput(f"invalid regular expression: {exc}", code="INVALID_REQUEST") from exc


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
