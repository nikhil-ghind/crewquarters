"""Download and verify the pinned speech models (run once, before serving)."""

from __future__ import annotations

import hashlib
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path

import httpx

from crewquarters_speech.catalog import ModelArchive


class DownloadError(RuntimeError):
    pass


def is_present(models_dir: Path, archive: ModelArchive) -> bool:
    root = models_dir / archive.directory
    return all((root / f).exists() for f in archive.files)


def download(
    archive: ModelArchive,
    models_dir: Path,
    *,
    client: httpx.Client | None = None,
    progress: Callable[[str], None] = print,
) -> Path:
    """Fetch, verify the SHA-256, and extract ``archive`` into ``models_dir`` (idempotent)."""
    models_dir.mkdir(parents=True, exist_ok=True)
    if is_present(models_dir, archive):
        progress(f"{archive.name}: already present")
        return models_dir / archive.directory
    http = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60, read=600))
    digest = hashlib.sha256()
    with tempfile.NamedTemporaryFile(dir=models_dir, suffix=".tar.bz2", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        try:
            with http.stream("GET", archive.url) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(1 << 20):
                    digest.update(chunk)
                    tmp.write(chunk)
        except httpx.HTTPError as exc:
            tmp_path.unlink(missing_ok=True)
            raise DownloadError(f"{archive.name}: download failed: {exc}") from exc
    try:
        if digest.hexdigest() != archive.sha256:
            raise DownloadError(
                f"{archive.name}: SHA-256 mismatch "
                f"(expected {archive.sha256}, got {digest.hexdigest()})"
            )
        progress(f"{archive.name}: verified, extracting")
        with tarfile.open(tmp_path, "r:bz2") as tar:
            tar.extractall(models_dir, filter="data")
    finally:
        tmp_path.unlink(missing_ok=True)
    if not is_present(models_dir, archive):
        raise DownloadError(f"{archive.name}: archive did not contain {archive.files}")
    return models_dir / archive.directory
