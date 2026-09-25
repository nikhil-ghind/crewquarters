from __future__ import annotations

import os
from pathlib import Path

import pytest

from crewquarters_secret_store import Keyring, SecretStoreError

pytestmark = pytest.mark.no_db

KEY1 = bytes(range(32))
KEY2 = bytes(range(32, 64))
CTX = {"provider": "google", "owner": "conn-1"}


def test_round_trip_uses_current_version() -> None:
    ring = Keyring({1: KEY1, 2: KEY2})
    blob = ring.encrypt(b"refresh-token-value", CTX)
    assert Keyring.key_version(blob) == 2
    assert ring.decrypt(blob, CTX) == b"refresh-token-value"
    assert b"refresh-token-value" not in blob


def test_same_plaintext_encrypts_differently() -> None:
    ring = Keyring({1: KEY1})
    assert ring.encrypt(b"x", CTX) != ring.encrypt(b"x", CTX)


def test_old_version_still_decrypts_after_rotation() -> None:
    blob = Keyring({1: KEY1}).encrypt(b"old", CTX)
    assert Keyring({1: KEY1, 2: KEY2}).decrypt(blob, CTX) == b"old"


def test_context_is_bound() -> None:
    ring = Keyring({1: KEY1})
    blob = ring.encrypt(b"secret", CTX)
    with pytest.raises(SecretStoreError, match="authentication"):
        ring.decrypt(blob, {"provider": "google", "owner": "conn-2"})
    with pytest.raises(SecretStoreError, match="authentication"):
        ring.decrypt(blob, {"provider": "twilio", "owner": "conn-1"})


def test_context_key_order_does_not_matter() -> None:
    ring = Keyring({1: KEY1})
    blob = ring.encrypt(b"secret", {"a": "1", "b": "2"})
    assert ring.decrypt(blob, {"b": "2", "a": "1"}) == b"secret"


def test_empty_context_is_refused() -> None:
    with pytest.raises(SecretStoreError, match="context"):
        Keyring({1: KEY1}).encrypt(b"secret", {})


@pytest.mark.parametrize("offset", [5, 20, 70, 80, -1])
def test_tampered_blob_fails(offset: int) -> None:
    ring = Keyring({1: KEY1})
    blob = bytearray(ring.encrypt(b"secret", CTX))
    blob[offset] ^= 1
    with pytest.raises(SecretStoreError, match="authentication"):
        ring.decrypt(bytes(blob), CTX)


def test_header_version_swap_fails() -> None:
    # Relabel a v1 blob as v2: the header is authenticated, so the v2 key must reject it.
    ring = Keyring({1: KEY1, 2: KEY1})
    blob = bytearray(Keyring({1: KEY1}).encrypt(b"secret", CTX))
    blob[1:5] = (2).to_bytes(4, "big")
    with pytest.raises(SecretStoreError, match="authentication"):
        ring.decrypt(bytes(blob), CTX)


def test_wrong_master_key_fails() -> None:
    blob = Keyring({1: KEY1}).encrypt(b"secret", CTX)
    with pytest.raises(SecretStoreError, match="authentication"):
        Keyring({1: KEY2}).decrypt(blob, CTX)


def test_unknown_version_and_bad_blobs() -> None:
    ring = Keyring({1: KEY1})
    blob = Keyring({7: KEY1}).encrypt(b"secret", CTX)
    with pytest.raises(SecretStoreError, match="version 7 is not loaded"):
        ring.decrypt(blob, CTX)
    with pytest.raises(SecretStoreError, match="truncated"):
        ring.decrypt(blob[:40], CTX)
    with pytest.raises(SecretStoreError, match="format"):
        ring.decrypt(b"\x02" + blob[1:], CTX)


@pytest.mark.parametrize("keys", [{}, {1: b"short"}, {0: KEY1}])
def test_invalid_keyrings(keys: dict[int, bytes]) -> None:
    with pytest.raises(SecretStoreError):
        Keyring(keys)


def test_repr_hides_key_material() -> None:
    text = repr(Keyring({1: KEY1}))
    assert KEY1.hex() not in text
    assert "versions=[1]" in text


def _key_file(tmp_path: Path, content: str, mode: int = 0o600) -> Path:
    path = tmp_path / "master.key"
    path.write_text(content)
    os.chmod(path, mode)
    return path


def test_from_file(tmp_path: Path) -> None:
    path = _key_file(tmp_path, f"# master keys\n1:{KEY1.hex()}\n\n2: {KEY2.hex()}\n")
    ring = Keyring.from_file(path)
    assert ring.current_version == 2
    assert ring.decrypt(Keyring({1: KEY1}).encrypt(b"s", CTX), CTX) == b"s"


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("", "empty"),
        ("1-abcd\n", "line 1 is malformed"),
        ("1:zz\n", "line 1 is malformed"),
        (f"1:{KEY1.hex()}\n1:{KEY2.hex()}\n", "repeats key version 1"),
        (f"1:{KEY1.hex()[:32]}\n", "not 256 bits"),
    ],
)
def test_from_file_rejects_bad_content(tmp_path: Path, content: str, match: str) -> None:
    with pytest.raises(SecretStoreError, match=match):
        Keyring.from_file(_key_file(tmp_path, content))


@pytest.mark.parametrize("mode", [0o604, 0o602])
def test_from_file_rejects_world_access(tmp_path: Path, mode: int) -> None:
    with pytest.raises(SecretStoreError, match="other users"):
        Keyring.from_file(_key_file(tmp_path, f"1:{KEY1.hex()}\n", mode))


def test_errors_never_echo_key_material(tmp_path: Path) -> None:
    secret_line = f"1:{KEY1.hex()}xyz\n"
    with pytest.raises(SecretStoreError) as info:
        Keyring.from_file(_key_file(tmp_path, secret_line))
    assert KEY1.hex() not in str(info.value)
