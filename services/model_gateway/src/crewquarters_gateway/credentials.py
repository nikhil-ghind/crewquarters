"""Cloud provider credentials and provider profiles.

Provider keys are stored encrypted by the secret store (``crewquarters_secret_store``,
PLAN.md section 10.2, ADR 0007). The capability broker only encrypts OpenAI/Anthropic
keys; the gateway alone decrypts them, in-process, with the device keyring
(``CQ_MASTER_KEY_FILE``). Without a keyring the gateway uses ``NoCredentials`` and every
cloud profile fails with ``NEEDS_CONNECTION``; there is no environment-variable key path,
so keys are never kept in plaintext configuration.

Decrypted keys never appear in logs, errors or reprs. They are cached in memory for at
most ``CQ_GATEWAY_CREDENTIAL_CACHE_SECONDS`` (default 60 s) so a key replaced or deleted
in Connections stops being used within that window.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_secret_store import Keyring, SecretStoreError
from crewquarters_secret_store import db as secret_db
from crewquarters_secret_store.db import ProviderProfile

log = logging.getLogger("crewquarters.gateway.credentials")

CLOUD_PROVIDERS = ("openai", "anthropic")


@dataclass(frozen=True)
class CloudProfile:
    """The non-secret part of a provider profile that routing needs."""

    provider: str
    profile_id: uuid.UUID | None = None
    secret_id: uuid.UUID | None = None
    allowed_models: tuple[str, ...] = ()
    budgets: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: ProviderProfile) -> CloudProfile:
        allowed = row.allowed_models if isinstance(row.allowed_models, list) else []
        budgets = row.budgets if isinstance(row.budgets, dict) else {}
        return cls(
            provider=row.provider,
            profile_id=row.id,
            secret_id=row.encrypted_secret_id,
            allowed_models=tuple(str(m) for m in allowed if isinstance(m, str) and m),
            budgets=dict(budgets),
        )

    def budget(self, name: str) -> int | None:
        """A positive integer budget such as ``dailyTokens``; anything else means unset."""
        value = self.budgets.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return None
        return value


class CredentialProvider(Protocol):
    async def profile(self, provider: str) -> CloudProfile | None:
        """The enabled, usable profile for ``provider`` (or None)."""
        ...

    async def api_key(
        self, provider: str, profile: CloudProfile | None = None, *, cached: bool = True
    ) -> str | None:
        """The decrypted key of ``profile`` (default: the active profile), or None."""
        ...


class NoCredentials:
    """Cloud disabled: no keyring is configured."""

    reason = "Cloud providers are disabled: the model gateway has no master key."

    async def profile(self, provider: str) -> CloudProfile | None:
        return None

    async def api_key(
        self, provider: str, profile: CloudProfile | None = None, *, cached: bool = True
    ) -> str | None:
        return None


class StaticCredentials:
    """Test helper: explicit in-memory keys (and optionally profile settings)."""

    def __init__(
        self, keys: dict[str, str], profiles: dict[str, CloudProfile] | None = None
    ) -> None:
        self._keys = keys
        self._profiles = profiles or {}

    def __repr__(self) -> str:
        return f"StaticCredentials(providers={sorted(self._keys)})"

    async def profile(self, provider: str) -> CloudProfile | None:
        if provider not in self._keys:
            return None
        return self._profiles.get(provider) or CloudProfile(provider=provider)

    async def api_key(
        self, provider: str, profile: CloudProfile | None = None, *, cached: bool = True
    ) -> str | None:
        return self._keys.get(provider)


class _Cached:
    __slots__ = ("expires", "value")
    value: str
    expires: float

    def __init__(self, value: str, expires: float) -> None:
        self.value = value
        self.expires = expires

    def __repr__(self) -> str:  # never show the key
        return "_Cached(<redacted>)"


class SecretStoreCredentials:
    """OpenAI/Anthropic keys from ``provider_profiles`` + ``encrypted_secrets``.

    The active profile for a provider is the enabled one whose status is not ``ERROR``,
    preferring ``CONNECTED`` over ``UNTESTED`` and then the newest.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        keyring: Keyring,
        cache_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sessions = sessions
        self._keyring = keyring
        self._cache_seconds = cache_seconds
        self._clock = clock
        self._cache: dict[uuid.UUID, _Cached] = {}

    def __repr__(self) -> str:
        return f"SecretStoreCredentials(keyring={self._keyring!r})"

    async def profile(self, provider: str) -> CloudProfile | None:
        if provider not in CLOUD_PROVIDERS:
            return None
        async with self._sessions() as db:
            row = await db.scalar(
                select(ProviderProfile)
                .where(
                    ProviderProfile.provider == provider,
                    ProviderProfile.enabled.is_(True),
                    ProviderProfile.status != "ERROR",
                    ProviderProfile.encrypted_secret_id.is_not(None),
                )
                .order_by(
                    case((ProviderProfile.status == "CONNECTED", 0), else_=1),
                    ProviderProfile.created_at.desc(),
                    ProviderProfile.id.desc(),
                )
                .limit(1)
            )
        return CloudProfile.from_row(row) if row is not None else None

    def forget(self, secret_id: uuid.UUID | None = None) -> None:
        """Drop cached keys (one secret, or all)."""
        if secret_id is None:
            self._cache.clear()
        else:
            self._cache.pop(secret_id, None)

    async def api_key(
        self, provider: str, profile: CloudProfile | None = None, *, cached: bool = True
    ) -> str | None:
        if provider not in CLOUD_PROVIDERS:
            return None
        if profile is None:
            profile = await self.profile(provider)
        if profile is None or profile.secret_id is None or profile.provider != provider:
            return None
        now = self._clock()
        hit = self._cache.get(profile.secret_id)
        if cached and hit is not None and hit.expires > now:
            return hit.value
        try:
            async with self._sessions() as db:
                plaintext = await secret_db.load(
                    db, self._keyring, profile.secret_id, provider=provider
                )
            key = plaintext.decode()
        except (SecretStoreError, UnicodeDecodeError) as exc:
            # The exception never carries key material; log only identifiers.
            log.warning(
                "provider key could not be decrypted",
                extra={
                    "event": "credentials.decrypt_failed",
                    "provider": provider,
                    "profile_id": str(profile.profile_id),
                    "reason": type(exc).__name__,
                },
            )
            self._cache.pop(profile.secret_id, None)
            return None
        if self._cache_seconds > 0:
            self._cache[profile.secret_id] = _Cached(key, now + self._cache_seconds)
        return key


def from_settings(
    sessions: async_sessionmaker[AsyncSession],
    master_key_file: str | None,
    cache_seconds: float,
) -> CredentialProvider:
    """``SecretStoreCredentials`` when a keyring file is configured, else ``NoCredentials``.

    A configured but unreadable/invalid keyring is a startup error, not a silent downgrade.
    """
    if not master_key_file:
        log.info(
            "CQ_MASTER_KEY_FILE is not set; cloud providers are disabled",
            extra={"event": "credentials.disabled"},
        )
        return NoCredentials()
    try:
        keyring = Keyring.from_file(master_key_file)
    except (OSError, SecretStoreError) as exc:
        raise RuntimeError(f"Cannot load the master keyring: {exc}") from None
    return SecretStoreCredentials(sessions, keyring, cache_seconds)
