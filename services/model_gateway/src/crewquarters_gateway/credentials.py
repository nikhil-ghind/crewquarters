"""Cloud provider credentials.

Provider keys are stored encrypted by the secret store that Nikhil Sajan Khaneja
(Person 3) owns; the gateway decrypts OpenAI/Anthropic keys in-process through that
shared library (PLAN.md section 10.2, ADR 0007). Until it exists, ``NoCredentials``
makes every cloud profile fail with ``CLOUD_PROVIDER_NOT_CONFIGURED``; there is no
environment-variable shortcut, so keys are never kept in plaintext configuration.
"""

from __future__ import annotations

from typing import Protocol


class CredentialProvider(Protocol):
    async def api_key(self, provider: str) -> str | None: ...


class NoCredentials:
    async def api_key(self, provider: str) -> str | None:
        return None


class StaticCredentials:
    """Test helper: explicit in-memory keys."""

    def __init__(self, keys: dict[str, str]) -> None:
        self._keys = keys

    async def api_key(self, provider: str) -> str | None:
        return self._keys.get(provider)
