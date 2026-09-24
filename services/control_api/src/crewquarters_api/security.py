"""Password hashing, opaque session tokens, CSRF tokens, and auth rate limiting."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Argon2id with argon2-cffi's RFC 9106 low-memory defaults.
_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("timing-equalizer-not-a-password")

SESSION_COOKIE = "cq_session"
CSRF_COOKIE = "cq_csrf"
CSRF_HEADER = "X-CSRF-Token"
MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-work verification; a missing user still pays for one Argon2 check."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(secret_key: str, session_token_hash: str) -> str:
    """Stateless CSRF token bound to the session (HMAC of the session hash)."""
    mac = hmac.new(secret_key.encode(), f"csrf:{session_token_hash}".encode(), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()).decode().rstrip("=")


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class RateLimiter:
    """In-process sliding-window limiter for bootstrap and login.

    One control-API process serves the appliance, so process memory is sufficient
    for v1. Multiple replicas would need a shared store.
    """

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def retry_after(self, key: str) -> int:
        hits = self._hits.get(key)
        if not hits:
            return 0
        return max(1, int(self.window - (time.monotonic() - hits[0])) + 1)
