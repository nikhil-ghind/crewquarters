"""Knowledge settings: the shared ``CQ_*`` settings plus knowledge-only variables.

Every variable is documented in ``docs/knowledge.md``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from crewquarters_shared.config import Settings


class KnowledgeSettings(Settings):
    # documents_dir (CQ_DOCUMENTS_DIR) is a shared setting: the control API reads it for backups.
    max_upload_bytes: int = 25 * 1024 * 1024
    embedding_mode: Literal["fake", "local"] = "fake"
    # Filled by ``cq-knowledge fetch-model``; the service only reads it, offline.
    embedding_model_dir: Path = Path("/var/lib/crewquarters/embedding-models")
    chunk_tokens: int = 800
    chunk_overlap_tokens: int = 120
    ingest_lease_seconds: int = 60
    ingest_poll_seconds: float = 1.0
    # Extraction runs in a child process with these bounds (see ``isolation``).
    extract_timeout_seconds: float = 120.0
    extract_memory_bytes: int = 1024 * 1024 * 1024
    # A whole ingestion (extract, chunk, embed, index) gives up after this long.
    ingest_max_seconds: float = 900.0
    # How often PENDING/PROCESSING documents without a live job are failed.
    ingest_sweep_seconds: float = 60.0


@lru_cache
def get_settings() -> KnowledgeSettings:
    return KnowledgeSettings()
