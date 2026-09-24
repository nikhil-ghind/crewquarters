"""UUIDv7 identifiers (time ordered) used for every platform record."""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Return an RFC 9562 UUIDv7: 48-bit Unix milliseconds, version, variant, random."""
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= ((rand >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= rand & ((1 << 62) - 1)
    return uuid.UUID(int=value)
