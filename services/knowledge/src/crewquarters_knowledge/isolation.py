"""Extraction and chunking in a child process (PLAN.md section 16.1, resource exhaustion).

Parsing an untrusted document can take unbounded time and memory: a 54 KB ``.docx`` can
expand to a multi-megabyte ``word/document.xml`` that takes python-docx half a minute.
:func:`prepare` therefore runs extraction and chunking in ``python -m
crewquarters_knowledge.isolation`` with:

* a wall-clock timeout (``CQ_EXTRACT_TIMEOUT_SECONDS``): the child is killed and the
  document fails with ``EXTRACTION_TIMEOUT``;
* an address-space limit (``RLIMIT_AS``, ``CQ_EXTRACT_MEMORY_BYTES``) and a CPU-time
  limit just above the timeout: running out fails the document with
  ``DOCUMENT_TOO_COMPLEX``;
* caps on the extracted characters and the number of chunks, and (in
  :mod:`crewquarters_knowledge.extract`) on the size of ``word/document.xml``, which also
  fail it with ``DOCUMENT_TOO_COMPLEX``.

The service runs in Linux containers (``amd64`` and ``arm64``), where these limits are
enforced. Elsewhere ``resource`` limits are applied as far as the platform allows.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crewquarters_knowledge.chunking import Chunk, chunk, count_tokens
from crewquarters_knowledge.extract import ExtractionError, extract

# About a thousand 800-token chunks; far beyond any document a demo appliance needs.
MAX_EXTRACTED_CHARS = 5_000_000
MAX_CHUNKS = 2_500
TOO_COMPLEX = (
    "This document needs more memory or processing than allowed. Split it into smaller files."
)


@dataclass(frozen=True)
class Limits:
    timeout_seconds: float
    memory_bytes: int
    max_chars: int = MAX_EXTRACTED_CHARS
    max_chunks: int = MAX_CHUNKS


@dataclass(frozen=True)
class Prepared:
    pieces: list[Chunk]
    segments: int
    tokens: int


async def prepare(
    path: Path, mime: str, chunk_tokens: int, overlap_tokens: int, limits: Limits
) -> Prepared:
    """Extract and chunk ``path`` in a bounded child process. Document problems raise
    :class:`ExtractionError`; a child that cannot start raises ``RuntimeError`` (retried)."""
    request = {
        "path": str(path),
        "mime": mime,
        "chunkTokens": chunk_tokens,
        "overlapTokens": overlap_tokens,
        "maxChars": limits.max_chars,
        "maxChunks": limits.max_chunks,
        "memoryBytes": limits.memory_bytes,
        "cpuSeconds": math.ceil(limits.timeout_seconds) + 1,
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "crewquarters_knowledge.isolation",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(json.dumps(request).encode()), limits.timeout_seconds
        )
    except TimeoutError:
        raise ExtractionError(
            "EXTRACTION_TIMEOUT",
            f"Reading this document took longer than {limits.timeout_seconds:g} seconds.",
        ) from None
    finally:
        if proc.returncode is None:  # timed out or cancelled: never leave it running
            proc.kill()
            await proc.wait()
    try:
        result: dict[str, Any] = json.loads(out)
    except ValueError:
        if proc.returncode is not None and proc.returncode < 0:  # killed by a limit
            raise ExtractionError("DOCUMENT_TOO_COMPLEX", TOO_COMPLEX) from None
        raise RuntimeError(f"extraction process failed with status {proc.returncode}") from None
    if "error" in result:
        raise ExtractionError(result["error"]["code"], result["error"]["message"])
    return Prepared(
        pieces=[Chunk(p["text"], p["tokenCount"], p["locator"]) for p in result["pieces"]],
        segments=result["segments"],
        tokens=result["tokens"],
    )


def _limit(memory_bytes: int, cpu_seconds: int) -> None:
    import resource

    for limit, value in ((resource.RLIMIT_AS, memory_bytes), (resource.RLIMIT_CPU, cpu_seconds)):
        # Linux enforces both; macOS, for one, refuses RLIMIT_AS.
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(limit, (value, value))


def _run(request: dict[str, Any]) -> dict[str, Any]:
    segments = extract(Path(request["path"]), request["mime"])
    if sum(len(s.text) for s in segments) > request["maxChars"]:
        raise ExtractionError("DOCUMENT_TOO_COMPLEX", TOO_COMPLEX)
    pieces = chunk(segments, request["chunkTokens"], request["overlapTokens"])
    if len(pieces) > request["maxChunks"]:
        raise ExtractionError("DOCUMENT_TOO_COMPLEX", TOO_COMPLEX)
    return {
        "pieces": [
            {"text": p.text, "tokenCount": p.token_count, "locator": p.locator} for p in pieces
        ],
        "segments": len(segments),
        "tokens": sum(count_tokens(s.text) for s in segments),
    }


def main() -> None:
    request = json.loads(sys.stdin.buffer.read())
    _limit(request["memoryBytes"], request["cpuSeconds"])
    try:
        result = _run(request)
    except ExtractionError as exc:
        result = {"error": {"code": exc.code, "message": exc.message}}
    except MemoryError:
        result = {"error": {"code": "DOCUMENT_TOO_COMPLEX", "message": TOO_COMPLEX}}
    except Exception:  # parsers raise many types for malformed files
        result = {
            "error": {"code": "EXTRACTION_FAILED", "message": "The document could not be read."}
        }
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
