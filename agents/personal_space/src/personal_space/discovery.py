"""Discovery: an agent cannot list documents, so it learns what a knowledge base holds by searching.

Round one runs broad probes (plus the owner's intent). Round two follows up on what came back,
using model-proposed queries and, as a fallback, the document names and headings already seen.
Every passage must clear the relevance cutoff, because the broker applies none.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crewquarters.errors import AgentError, PlatformError
from crewquarters.knowledge import Passage
from personal_space.config import PersonalSpaceConfig

PROBES = (
    "overview and main topics",
    "key facts and important details",
    "goals and priorities",
    "upcoming deadlines and next steps",
    "open questions and things to explore",
)
MAX_QUERY_CHARS = 300
SEARCH_CONCURRENCY = 3
CONTEXT_TOKENS = 5000
PER_PASSAGE_CHARS = 900


@dataclass
class Retrieved:
    """Distinct passages that cleared the cutoff, by citation id, and how the search went."""

    passages: dict[str, Passage] = field(default_factory=dict)
    queries: list[str] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)

    def add(self, found: Iterable[Passage], min_relevance: float) -> None:
        for passage in found:
            self.seen.add(passage.citation_id)
            if passage.score < min_relevance:
                continue
            current = self.passages.get(passage.citation_id)
            if current is None or passage.score > current.score:
                self.passages[passage.citation_id] = passage

    def cap(self, limit: int) -> None:
        best = sorted(self.passages.values(), key=lambda p: p.score, reverse=True)[:limit]
        self.passages = {p.citation_id: p for p in best}

    @property
    def documents(self) -> set[str]:
        return {p.document_id for p in self.passages.values()}


def clean_query(query: str) -> str:
    return " ".join(query.split())[:MAX_QUERY_CHARS]


def probe_queries(intent: str | None) -> list[str]:
    return [clean_query(intent), *PROBES] if intent else list(PROBES)


def _label(passage: Passage) -> str:
    section = passage.locator.get("section")
    return f"{passage.document_name} § {section}" if section else passage.document_name


def derive_queries(passages: Iterable[Passage], used: Iterable[str], limit: int) -> list[str]:
    """Follow-up queries from headings (or document names) of what has already been found."""
    taken = {q.lower() for q in used}
    found: list[str] = []
    for passage in sorted(passages, key=lambda p: p.score, reverse=True):
        section = passage.locator.get("section")
        stem = Path(passage.document_name).stem.replace("-", " ").replace("_", " ")
        query = clean_query(str(section) if section else stem)
        if query and query.lower() not in taken:
            taken.add(query.lower())
            found.append(query)
        if len(found) >= limit:
            break
    return found


def select_for_prompt(passages: Iterable[Passage], max_chars: int) -> list[Passage]:
    """Fill the prompt budget round-robin across documents, so none crowds out the rest."""
    by_document: dict[str, list[Passage]] = {}
    for passage in sorted(passages, key=lambda p: p.score, reverse=True):
        by_document.setdefault(passage.document_id, []).append(passage)
    queues = sorted(by_document.values(), key=lambda q: q[0].score, reverse=True)
    chosen: list[Passage] = []
    used = 0
    while any(queues):
        for queue in queues:
            if not queue:
                continue
            passage = queue.pop(0)
            cost = min(len(passage.text), PER_PASSAGE_CHARS)
            if chosen and used + cost > max_chars:
                return chosen
            chosen.append(passage)
            used += cost
    return chosen


def prompt_refs(selected: list[Passage]) -> tuple[dict[str, tuple[str, str]], dict[str, Passage]]:
    """Short refs p1..pN for the model. Returns (ref -> (source, text), ref -> passage)."""
    refs = {f"p{i + 1}": p for i, p in enumerate(selected)}
    shown = {ref: (_label(p), p.text[:PER_PASSAGE_CHARS]) for ref, p in refs.items()}
    return shown, refs


async def _search_one(ctx: Any, config: PersonalSpaceConfig, query: str) -> list[Passage]:
    try:
        result = await ctx.knowledge.search(
            config.knowledge_base_id,
            query,
            top_k=config.top_k,
            max_context_tokens=CONTEXT_TOKENS,
        )
    except PlatformError as exc:
        if exc.code == "NEEDS_CONFIGURATION":
            raise AgentError(
                "Choose a knowledge base in this agent's configuration",
                code="KNOWLEDGE_BASE_REQUIRED",
            ) from exc
        if exc.code == "NOT_FOUND":
            raise AgentError(
                "The selected knowledge base no longer exists", code="KNOWLEDGE_BASE_NOT_FOUND"
            ) from exc
        raise
    passages: list[Passage] = result.passages
    return passages


async def run_queries(
    ctx: Any, config: PersonalSpaceConfig, queries: list[str], retrieved: Retrieved
) -> None:
    """Search each new query (bounded parallelism) and merge what clears the cutoff."""
    ran = {q.lower() for q in retrieved.queries}
    fresh: list[str] = []
    for query in queries:
        if (
            query
            and query.lower() not in ran
            and len(retrieved.queries) + len(fresh) < config.max_queries
        ):
            ran.add(query.lower())
            fresh.append(query)
    limit = asyncio.Semaphore(SEARCH_CONCURRENCY)

    async def one(query: str) -> list[Passage]:
        async with limit:
            return await _search_one(ctx, config, query)

    batches = await asyncio.gather(*(one(query) for query in fresh))
    for query, found in zip(fresh, batches, strict=True):
        retrieved.queries.append(query)
        retrieved.add(found, config.min_relevance)
    retrieved.cap(config.max_passages)
