"""Knowledge settings: the shared ``CQ_*`` settings plus knowledge-only variables.

Every variable is documented in ``docs/knowledge.md``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from crewquarters_shared.config import Settings


class KnowledgeSettings(Settings):
    documents_dir: Path = Path("/var/lib/crewquarters/documents")
    max_upload_bytes: int = 25 * 1024 * 1024
    embedding_mode: Literal["fake", "local"] = "fake"
    embedding_cache_dir: Path | None = None
    chunk_tokens: int = 800
    chunk_overlap_tokens: int = 120
    ingest_lease_seconds: int = 60
    ingest_poll_seconds: float = 1.0


@lru_cache
def get_settings() -> KnowledgeSettings:
    return KnowledgeSettings()
