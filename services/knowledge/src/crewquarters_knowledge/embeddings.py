"""Embedding profiles (PLAN.md section 9.1, step 7, and 16.1: pinned model revisions).

* ``local`` — ``jinaai/jina-embeddings-v2-small-en`` through ``fastembed`` (ONNX Runtime;
  ``amd64`` and ``arm64`` wheels): 512 dimensions, an 8192 token context (so an 800-token
  chunk is never truncated), Apache-2.0, ~130 MB. fastembed runs the ONNX export in the
  Hugging Face repository ``Xenova/jina-embeddings-v2-small-en``. The service never
  downloads it: ``cq-knowledge fetch-model`` (:func:`fetch`) fetches the files of one
  pinned commit, checks each file's SHA-256, and stores them under
  ``CQ_EMBEDDING_MODEL_DIR``. The service verifies them again and loads them at startup,
  offline. Until they are present, readiness fails with ``EMBEDDING_MODEL_UNAVAILABLE``.
* ``fake`` — deterministic feature hashing with the same dimension, for tests and the
  laptop ``dev`` profile. It captures word overlap, not meaning.

A profile id stands for exactly one model revision (:data:`PROFILES`). Moving to another
revision needs a new profile id, so existing knowledge bases report
``EMBEDDING_PROFILE_MISMATCH`` until they are re-indexed.

Embedding is synchronous; callers run it in a worker thread.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import math
import os
import re
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from crewquarters_knowledge.models import EMBEDDING_DIMENSION
from crewquarters_shared.errors import PlatformError

log = logging.getLogger("crewquarters.knowledge.embeddings")

LOCAL_MODEL = "jinaai/jina-embeddings-v2-small-en"  # fastembed's name for the model
LOCAL_PROFILE = "local.embedding.jina-v2-small-en"
HF_BASE_URL = "https://huggingface.co"
HF_REPO = "Xenova/jina-embeddings-v2-small-en"
# The repository's ``main`` as of 2025-04-24, the export fastembed 0.8 downloads.
HF_REVISION = "523cadcb9c2e71c7153fc46016e1fe79acb4f58f"
# The files fastembed reads, with their sizes and SHA-256 at that revision.
MODEL_FILES: dict[str, tuple[int, str]] = {
    "config.json": (1150, "7472dfdbfafa39df639cc5e8a23a15f2bd7d26adfde3eb2d6da3612435c39f8e"),
    "tokenizer.json": (
        711573,
        "e9f999ac74497843ed9f4303246a8f43d9f100ee8aab8e133667903f447ceb48",
    ),
    "tokenizer_config.json": (
        367,
        "63d41f24b6076d8f189a9f6e7017655a8596f4f536a06bac5cc4da2c79f2b49d",
    ),
    "special_tokens_map.json": (
        125,
        "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3",
    ),
    "onnx/model.onnx": (
        129799236,
        "8daf59cab0f24c7e0231a1e3ff9c348a97f6662e9a763dd4f15d2ca2f1614e05",
    ),
}
PROFILES: dict[str, dict[str, Any]] = {
    LOCAL_PROFILE: {
        "model": LOCAL_MODEL,
        "source": f"huggingface:{HF_REPO}",
        "revision": HF_REVISION,
        "dimension": EMBEDDING_DIMENSION,
    },
    "fake.hashing-512": {"model": "feature-hashing", "dimension": EMBEDDING_DIMENSION},
}
BATCH_SIZE = 32
READ_BLOCK = 1024 * 1024
_WORD = re.compile(r"\w+")


class ModelUnavailable(Exception):
    """The pinned model files are missing or do not match their checksums."""


class ChecksumMismatch(ModelUnavailable):
    """A downloaded file is not the pinned file."""


class Embedder(Protocol):
    profile: str
    dimension: int

    @property
    def ready(self) -> bool: ...

    def load(self) -> None: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbedder:
    profile = "fake.hashing-512"
    dimension = EMBEDDING_DIMENSION
    ready = True

    def load(self) -> None:
        pass

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


def model_path(root: Path) -> Path:
    """Where the pinned revision lives under ``CQ_EMBEDDING_MODEL_DIR``."""
    return root / f"{HF_REPO.rsplit('/', 1)[1]}@{HF_REVISION}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(READ_BLOCK):
            digest.update(block)
    return digest.hexdigest()


def _matches(path: Path, size: int, sha256: str) -> bool:
    return path.is_file() and path.stat().st_size == size and _sha256(path) == sha256


def verify(directory: Path, files: Mapping[str, tuple[int, str]] | None = None) -> None:
    """Raise :class:`ModelUnavailable` unless every pinned file is present and intact."""
    for name, (size, sha256) in (files or MODEL_FILES).items():
        path = directory / name
        if not path.is_file():
            raise ModelUnavailable(
                f"The embedding model is not installed ({name} is missing from {directory}). "
                "Run `cq-knowledge fetch-model`."
            )
        if not _matches(path, size, sha256):
            raise ModelUnavailable(
                f"The embedding model file {name} does not match its pinned checksum. "
                "Run `cq-knowledge fetch-model` to replace it."
            )


def fetch(
    root: Path,
    *,
    base_url: str = HF_BASE_URL,
    files: Mapping[str, tuple[int, str]] | None = None,
    timeout_seconds: float = 60.0,
) -> Path:
    """Download the pinned revision's files into :func:`model_path` and verify each one.

    Idempotent: files already present and intact are not downloaded again. A file whose
    SHA-256 (or size) differs is discarded and :class:`ChecksumMismatch` is raised, so a
    partial or tampered download is never used. Returns the model directory."""
    directory = model_path(root)
    for name, (size, sha256) in (files or MODEL_FILES).items():
        target = directory / name
        if _matches(target, size, sha256):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        url = f"{base_url.rstrip('/')}/{HF_REPO}/resolve/{HF_REVISION}/{name}"
        log.info("downloading %s", url)
        digest, received = hashlib.sha256(), 0
        try:
            with (
                urllib.request.urlopen(url, timeout=timeout_seconds) as resp,  # noqa: S310 - https base URL
                part.open("wb") as out,
            ):
                while block := resp.read(READ_BLOCK):
                    received += len(block)
                    if received > size:
                        break
                    digest.update(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
            if received != size or digest.hexdigest() != sha256:
                raise ChecksumMismatch(
                    f"{name} from {url} does not match the pinned SHA-256 {sha256}."
                )
            os.replace(part, target)
        finally:
            with contextlib.suppress(FileNotFoundError):
                part.unlink()
    verify(directory, files)
    return directory


class LocalEmbedder:
    profile = LOCAL_PROFILE
    dimension = EMBEDDING_DIMENSION

    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_path(model_dir)
        self._model: Any = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Verify the pinned files, then load them without any network access. Raises
        :class:`ModelUnavailable` when they are missing or altered."""
        verify(self.model_dir)
        os.environ["HF_HUB_OFFLINE"] = "1"  # never fall back to a download
        from fastembed import TextEmbedding

        self._model = TextEmbedding(
            LOCAL_MODEL,
            cache_dir=str(self.model_dir),
            specific_model_path=str(self.model_dir),
            local_files_only=True,
        )
        log.info("embedding model loaded", extra={"revision": HF_REVISION})

    def _loaded(self) -> Any:
        if self._model is None:
            raise PlatformError(
                "EMBEDDING_MODEL_UNAVAILABLE",
                "The embedding model is not installed. Run `cq-knowledge fetch-model`.",
                503,
            )
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._loaded().passage_embed(texts, batch_size=BATCH_SIZE)]

    def embed_query(self, text: str) -> list[float]:
        return [float(x) for x in next(iter(self._loaded().query_embed(text)))]


def create(mode: str, model_dir: Path) -> Embedder:
    return LocalEmbedder(model_dir) if mode == "local" else HashingEmbedder()
