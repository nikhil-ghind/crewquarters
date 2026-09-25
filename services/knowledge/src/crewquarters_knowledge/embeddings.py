"""Embedding profiles (PLAN.md section 9.1, step 7).

* ``local`` — the pinned CPU model ``jinaai/jina-embeddings-v2-small-en`` through
  ``fastembed`` (ONNX Runtime; ``amd64`` and ``arm64`` wheels): 512 dimensions, an 8192
  token context (so an 800-token chunk is never truncated), Apache-2.0, ~120 MB.
* ``fake`` — deterministic feature hashing with the same dimension, for tests and the
  laptop ``dev`` profile. It captures word overlap, not meaning.

Both are synchronous; callers run them in a worker thread.
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any, Protocol

from crewquarters_knowledge.models import EMBEDDING_DIMENSION

LOCAL_MODEL = "jinaai/jina-embeddings-v2-small-en"
BATCH_SIZE = 32
_WORD = re.compile(r"\w+")


class Embedder(Protocol):
    profile: str
    dimension: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbedder:
    profile = "fake.hashing-512"
    dimension = EMBEDDING_DIMENSION

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for word in _WORD.findall(text.lower()):
            digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            vector[value % self.dimension] += 1.0 if value >> 63 else -1.0
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:  # a zero vector has no cosine distance
            vector[0], norm = 1.0, 1.0
        return [v / norm for v in vector]


class LocalEmbedder:
    profile = "local.embedding.jina-v2-small-en"
    dimension = EMBEDDING_DIMENSION

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(
                LOCAL_MODEL, cache_dir=str(self._cache_dir) if self._cache_dir else None
            )
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._load().passage_embed(texts, batch_size=BATCH_SIZE)]

    def embed_query(self, text: str) -> list[float]:
        return [float(x) for x in next(iter(self._load().query_embed(text)))]


def create(mode: str, cache_dir: Path | None) -> Embedder:
    return LocalEmbedder(cache_dir) if mode == "local" else HashingEmbedder()
