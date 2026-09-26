"""Knowledge-base stand-in: fixture documents, paragraph chunks, BM25-lite ranking."""

from __future__ import annotations

import fnmatch
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED = frozenset({".md", ".txt"})
CHUNK_CHARS = 800
_STOPWORDS = frozenset(
    "a an and are as at be by do for from how i in is it of on or the to what when with".split()  # noqa: SIM905
)


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS]


@dataclass
class Chunk:
    kb_id: str
    doc_id: str
    doc_name: str
    ordinal: int
    text: str
    section: str | None
    terms: Counter[str]


def split_sections(text: str) -> list[tuple[str | None, str]]:
    """Split a document into (section heading, chunk text) pairs of about CHUNK_CHARS at most."""
    chunks: list[tuple[str | None, str]] = []
    section: str | None = None
    buffer: list[str] = []
    for paragraph in (p.strip() for p in re.split(r"\n\s*\n", text)):
        if not paragraph:
            continue
        heading = re.match(r"^#+\s+(.*)$", paragraph)
        if heading or (buffer and sum(map(len, buffer)) + len(paragraph) > CHUNK_CHARS):
            if buffer:
                chunks.append((section, "\n\n".join(buffer)))
            buffer = []
        if heading:
            section = heading.group(1).strip()
            continue
        buffer.append(paragraph)
    if buffer:
        chunks.append((section, "\n\n".join(buffer)))
    return chunks


class KnowledgeIndex:
    def __init__(self) -> None:
        self.kbs: dict[str, list[Chunk]] = {}

    def has(self, kb_id: str) -> bool:
        return kb_id in self.kbs

    def load_dir(self, kb_id: str, path: Path) -> None:
        chunks: list[Chunk] = []
        for file in sorted(Path(path).iterdir()):
            if file.suffix.lower() not in SUPPORTED or not file.is_file():
                continue
            doc_id = re.sub(r"[^a-z0-9-]+", "-", file.stem.lower()).strip("-")
            text = file.read_text(encoding="utf-8", errors="replace")
            for section, body in split_sections(text):
                chunks.append(
                    Chunk(
                        kb_id,
                        doc_id,
                        file.name,
                        len(chunks),
                        body,
                        section,
                        Counter(tokenize(body)),
                    )
                )
        self.kbs[kb_id] = chunks

    def documents(self, kb_id: str, pattern: str = "*") -> list[dict[str, Any]]:
        """Distinct documents whose file name matches a case-insensitive glob, newest first."""
        docs: dict[str, dict[str, Any]] = {}
        for chunk in self.kbs.get(kb_id, []):
            doc = docs.setdefault(
                chunk.doc_id,
                {
                    "id": chunk.doc_id,
                    "name": chunk.doc_name,
                    "mime": "text/markdown" if chunk.doc_name.endswith(".md") else "text/plain",
                    "bytes": 0,
                },
            )
            doc["bytes"] += len(chunk.text.encode())
        matching = [
            d for d in docs.values() if fnmatch.fnmatchcase(d["name"].lower(), pattern.lower())
        ]
        return list(reversed(matching))

    def search(
        self, kb_id: str, query: str, top_k: int = 8, document_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        chunks = [
            c for c in self.kbs.get(kb_id, []) if not document_ids or c.doc_id in document_ids
        ]
        terms = tokenize(query)
        if not chunks or not terms:
            return []
        average = sum(sum(c.terms.values()) for c in chunks) / len(chunks)
        scored = []
        for chunk in chunks:
            length = sum(chunk.terms.values())
            score = 0.0
            for term in set(terms):
                frequency = chunk.terms.get(term, 0)
                if not frequency:
                    continue
                documents = sum(1 for c in chunks if term in c.terms)
                idf = math.log(1 + (len(chunks) - documents + 0.5) / (documents + 0.5))
                score += (
                    idf * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * length / average))
                )
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {
                "citationId": f"kb:{kb_id}:doc:{chunk.doc_id}:chunk:{chunk.ordinal}",
                "text": chunk.text,
                "score": round(score, 4),
                "document": {"id": chunk.doc_id, "name": chunk.doc_name},
                "locator": {"section": chunk.section} if chunk.section else {},
            }
            for score, chunk in scored[:top_k]
        ]
