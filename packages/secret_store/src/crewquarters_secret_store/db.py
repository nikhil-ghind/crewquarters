"""Access module for the ``encrypted_secrets`` and ``provider_profiles`` tables.

Owner: Nikhil Sajan Khaneja (Person 3). The capability broker stores Google and Twilio
secrets; the model gateway reads OpenAI and Anthropic keys. Each service decrypts only
its own providers' secrets, in-process, with :class:`~crewquarters_secret_store.Keyring`.

The AAD context binds every ciphertext to its row id, provider, and owner, so a
ciphertext copied into another row, or read as another provider's secret, fails.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

import crewquarters_shared.db.models  # noqa: F401 - registers users, the owner_id foreign key target
from crewquarters_secret_store import Keyring, SecretStoreError
from crewquarters_shared.db.base import Base
from crewquarters_shared.ids import uuid7

PROVIDERS = ("google", "twilio", "openai", "anthropic")
_PROVIDER_CHECK = "provider IN ('google', 'twilio', 'openai', 'anthropic')"


class EncryptedSecret(Base):
    __tablename__ = "encrypted_secrets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    owner_type: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(_PROVIDER_CHECK, name="provider"),
        UniqueConstraint("owner_type", "owner_id"),
    )

    def __repr__(self) -> str:  # never include ciphertext
        return f"EncryptedSecret(id={self.id}, provider={self.provider}, owner={self.owner_type})"


class ProviderProfile(Base):
    """A configured API-key provider (OpenAI, Anthropic) or Twilio account."""

    __tablename__ = "provider_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_secret_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("encrypted_secrets.id", ondelete="SET NULL")
    )
    # Non-secret provider settings, e.g. the last four digits of a Twilio number.
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    allowed_models: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    budgets: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="UNTESTED")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(_PROVIDER_CHECK, name="provider"),
        CheckConstraint("status IN ('UNTESTED', 'CONNECTED', 'ERROR')", name="status"),
        UniqueConstraint("owner_id", "provider", "display_name"),
    )


def _context(secret: EncryptedSecret) -> dict[str, str]:
    return {
        "id": str(secret.id),
        "provider": secret.provider,
        "owner": f"{secret.owner_type}:{secret.owner_id}",
    }


async def store(
    session: AsyncSession,
    keyring: Keyring,
    *,
    provider: str,
    owner_type: str,
    owner_id: uuid.UUID,
    plaintext: bytes,
) -> EncryptedSecret:
    """Encrypt and add a new secret row. The caller commits."""
    secret = EncryptedSecret(
        id=uuid7(), provider=provider, owner_type=owner_type, owner_id=owner_id
    )
    secret.ciphertext = keyring.encrypt(plaintext, _context(secret))
    secret.key_version = keyring.current_version
    session.add(secret)
    await session.flush()
    return secret


async def load(
    session: AsyncSession, keyring: Keyring, secret_id: uuid.UUID, *, provider: str
) -> bytes:
    """Decrypt one secret. ``provider`` must match, so a service reads only its own."""
    secret = await session.get(EncryptedSecret, secret_id)
    if secret is None or secret.provider != provider:
        raise SecretStoreError("secret not found")
    return keyring.decrypt(secret.ciphertext, _context(secret))


async def replace(
    session: AsyncSession, keyring: Keyring, secret_id: uuid.UUID, plaintext: bytes
) -> None:
    """Re-encrypt an existing secret with new plaintext and the current key version."""
    secret = await session.get(EncryptedSecret, secret_id)
    if secret is None:
        raise SecretStoreError("secret not found")
    secret.ciphertext = keyring.encrypt(plaintext, _context(secret))
    secret.key_version = keyring.current_version


async def delete(session: AsyncSession, secret_id: uuid.UUID) -> None:
    secret = await session.get(EncryptedSecret, secret_id)
    if secret is not None:
        await session.delete(secret)
