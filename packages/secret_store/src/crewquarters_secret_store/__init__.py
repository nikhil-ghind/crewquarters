"""Envelope encryption of provider secrets (PLAN.md section 10.2).

Each secret gets a random 256-bit data key. The data key encrypts the secret with
AES-256-GCM, and the versioned device master key wraps the data key with AES-256-GCM.
Both operations bind the blob header and the caller's context (provider and owner
identifiers) as associated data, so a ciphertext cannot be replayed under another
owner or key version.

Only the capability broker (Google, Twilio) and the model gateway (OpenAI, Anthropic)
load the master key. Each decrypts its own secrets in-process; plaintext never
travels between services.

Blob layout (bytes)::

    format(1) | key_version(4, big-endian) | wrap_nonce(12) | wrapped_key(48)
    | nonce(12) | ciphertext
"""

from __future__ import annotations

import json
import os
import stat
import struct
from collections.abc import Mapping
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

__all__ = ["Keyring", "SecretStoreError"]

FORMAT = 1
KEY_BYTES = 32
NONCE_BYTES = 12
_HEADER = struct.Struct(">BI")
_WRAPPED_BYTES = KEY_BYTES + 16  # AES-GCM appends a 16-byte tag
_MIN_BLOB = _HEADER.size + NONCE_BYTES + _WRAPPED_BYTES + NONCE_BYTES + 16


class SecretStoreError(Exception):
    """Invalid key material, or a blob that fails authentication. Never carries secrets."""


def _aad(header: bytes, context: Mapping[str, str]) -> bytes:
    if not context:
        raise SecretStoreError("context must identify the provider and owner")
    return header + json.dumps(dict(context), sort_keys=True, separators=(",", ":")).encode()


class Keyring:
    """Master keys by version. New secrets use the highest version."""

    __slots__ = ("_keys", "current_version")

    def __init__(self, keys: Mapping[int, bytes]) -> None:
        if not keys:
            raise SecretStoreError("keyring is empty")
        for version, key in keys.items():
            if not 1 <= version <= 0xFFFFFFFF:
                raise SecretStoreError(f"key version {version} is out of range")
            if len(key) != KEY_BYTES:
                raise SecretStoreError(f"key version {version} is not {KEY_BYTES * 8} bits")
        self._keys = {v: AESGCM(k) for v, k in keys.items()}
        self.current_version = max(keys)

    def __repr__(self) -> str:
        return f"Keyring(versions={sorted(self._keys)}, current={self.current_version})"

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> Keyring:
        """Load ``<version>:<64 hex chars>`` lines. Blank lines and ``#`` comments are ignored.

        The file must not be readable or writable by other users.
        """
        file = Path(path)
        if file.stat().st_mode & (stat.S_IROTH | stat.S_IWOTH):
            raise SecretStoreError(f"{file} must not be accessible to other users")
        keys: dict[int, bytes] = {}
        for number, raw in enumerate(file.read_text().splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                version_text, key_hex = line.split(":", 1)
                version, key = int(version_text), bytes.fromhex(key_hex.strip())
            except ValueError:
                raise SecretStoreError(f"{file} line {number} is malformed") from None
            if version in keys:
                raise SecretStoreError(f"{file} repeats key version {version}")
            keys[version] = key
        return cls(keys)

    def encrypt(self, plaintext: bytes, context: Mapping[str, str]) -> bytes:
        header = _HEADER.pack(FORMAT, self.current_version)
        aad = _aad(header, context)
        data_key = AESGCM.generate_key(bit_length=KEY_BYTES * 8)
        wrap_nonce, nonce = os.urandom(NONCE_BYTES), os.urandom(NONCE_BYTES)
        wrapped = self._keys[self.current_version].encrypt(wrap_nonce, data_key, aad)
        ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, aad)
        return header + wrap_nonce + wrapped + nonce + ciphertext

    def decrypt(self, blob: bytes, context: Mapping[str, str]) -> bytes:
        version = self.key_version(blob)
        master = self._keys.get(version)
        if master is None:
            raise SecretStoreError(f"key version {version} is not loaded")
        header, rest = blob[: _HEADER.size], blob[_HEADER.size :]
        aad = _aad(header, context)
        wrap_nonce, rest = rest[:NONCE_BYTES], rest[NONCE_BYTES:]
        wrapped, rest = rest[:_WRAPPED_BYTES], rest[_WRAPPED_BYTES:]
        nonce, ciphertext = rest[:NONCE_BYTES], rest[NONCE_BYTES:]
        try:
            data_key = master.decrypt(wrap_nonce, wrapped, aad)
            return AESGCM(data_key).decrypt(nonce, ciphertext, aad)
        except InvalidTag:
            raise SecretStoreError("secret failed authentication") from None

    @staticmethod
    def key_version(blob: bytes) -> int:
        """The master key version that sealed ``blob``, for storage and rotation queries."""
        if len(blob) < _MIN_BLOB:
            raise SecretStoreError("secret blob is truncated")
        fmt, version = _HEADER.unpack_from(blob)
        if fmt != FORMAT:
            raise SecretStoreError(f"unknown secret format {fmt}")
        return int(version)
