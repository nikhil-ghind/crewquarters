from __future__ import annotations

import uuid

import pytest
from crewquarters_secret_store import Keyring, SecretStoreError
from crewquarters_secret_store import db as secrets
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.usefixtures("person3_secret_tables")

RING = Keyring({1: bytes(32)})
OWNER = uuid.UUID(int=1)


async def _store(
    db: AsyncSession, provider: str = "google", plaintext: bytes = b"tok"
) -> uuid.UUID:
    row = await secrets.store(
        db,
        RING,
        provider=provider,
        owner_type="oauth_connection",
        owner_id=OWNER,
        plaintext=plaintext,
    )
    await db.commit()
    return row.id


async def test_store_and_load(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as db:
        secret_id = await _store(db)
    async with sessions() as db:
        row = await db.get(secrets.EncryptedSecret, secret_id)
        assert row is not None and row.key_version == 1
        assert b"tok" not in row.ciphertext and "tok" not in repr(row)
        assert await secrets.load(db, RING, secret_id, provider="google") == b"tok"


async def test_other_provider_cannot_read(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as db:
        secret_id = await _store(db, provider="openai")
        with pytest.raises(SecretStoreError, match="not found"):
            await secrets.load(db, RING, secret_id, provider="google")
        with pytest.raises(SecretStoreError, match="not found"):
            await secrets.load(db, RING, uuid.uuid4(), provider="openai")


async def test_ciphertext_copied_to_another_row_fails(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as db:
        first = await _store(db, plaintext=b"victim")
        second = await secrets.store(
            db,
            RING,
            provider="google",
            owner_type="oauth_connection",
            owner_id=uuid.uuid4(),
            plaintext=b"attacker",
        )
        victim = await db.get(secrets.EncryptedSecret, first)
        assert victim is not None
        second.ciphertext = victim.ciphertext
        await db.commit()
        with pytest.raises(SecretStoreError, match="authentication"):
            await secrets.load(db, RING, second.id, provider="google")


async def test_replace_rotates_to_current_key(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as db:
        secret_id = await _store(db)
    rotated = Keyring({1: bytes(32), 2: bytes(range(32))})
    async with sessions() as db:
        await secrets.replace(db, rotated, secret_id, b"new")
        await db.commit()
        row = await db.get(secrets.EncryptedSecret, secret_id)
        assert row is not None and row.key_version == 2
        assert await secrets.load(db, rotated, secret_id, provider="google") == b"new"
        with pytest.raises(SecretStoreError, match="not found"):
            await secrets.replace(db, rotated, uuid.uuid4(), b"x")


async def test_delete(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as db:
        secret_id = await _store(db)
        await secrets.delete(db, secret_id)
        await secrets.delete(db, uuid.uuid4())  # missing is a no-op
        await db.commit()
        with pytest.raises(SecretStoreError, match="not found"):
            await secrets.load(db, RING, secret_id, provider="google")
