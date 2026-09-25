"""The pinned embedding model: checksum-verified fetch, offline load, and the CLI.

No test touches the network: a local HTTP server stands in for Hugging Face."""

from __future__ import annotations

import hashlib
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from crewquarters_knowledge import embeddings, main
from crewquarters_shared.errors import PlatformError

pytestmark = pytest.mark.no_db

FILES = {"config.json": b'{"model": "tiny"}', "onnx/model.onnx": b"\x08\x01" * 5000}


def pinned(files: dict[str, bytes]) -> dict[str, tuple[int, str]]:
    return {name: (len(data), hashlib.sha256(data).hexdigest()) for name, data in files.items()}


class Hub:
    """Serves ``/<repo>/resolve/<revision>/<file>`` like huggingface.co and counts requests."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = dict(files)
        self.requests: list[str] = []
        hub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                hub.requests.append(self.path)
                prefix = f"/{embeddings.HF_REPO}/resolve/{embeddings.HF_REVISION}/"
                data = hub.files.get(self.path.removeprefix(prefix))
                if not self.path.startswith(prefix) or data is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: object) -> None:
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> Iterator[Hub]:
    monkeypatch.setattr(embeddings, "MODEL_FILES", pinned(FILES))
    server = Hub(FILES)
    yield server
    server.server.shutdown()


def test_the_real_pin_is_a_full_commit_with_sha256_per_file() -> None:
    assert len(embeddings.HF_REVISION) == 40 and int(embeddings.HF_REVISION, 16) >= 0
    assert set(embeddings.MODEL_FILES) >= {"onnx/model.onnx", "tokenizer.json", "config.json"}
    for size, sha256 in embeddings.MODEL_FILES.values():
        assert size > 0 and len(sha256) == 64 and int(sha256, 16) >= 0
    profile = embeddings.PROFILES[embeddings.LOCAL_PROFILE]
    assert profile["revision"] == embeddings.HF_REVISION
    assert profile["source"] == "huggingface:Xenova/jina-embeddings-v2-small-en"


def test_fetch_verifies_and_is_idempotent(hub: Hub, tmp_path: Path) -> None:
    directory = embeddings.fetch(tmp_path, base_url=hub.url)
    assert directory == embeddings.model_path(tmp_path)
    assert directory.name == f"jina-embeddings-v2-small-en@{embeddings.HF_REVISION}"
    for name, data in FILES.items():
        assert (directory / name).read_bytes() == data
    assert len(hub.requests) == 2
    embeddings.fetch(tmp_path, base_url=hub.url)
    assert len(hub.requests) == 2  # nothing downloaded again
    (directory / "config.json").write_bytes(b'{"model": "tampered"}')
    embeddings.fetch(tmp_path, base_url=hub.url)
    assert (directory / "config.json").read_bytes() == FILES["config.json"]
    assert len(hub.requests) == 3  # only the altered file


def test_fetch_rejects_a_file_that_does_not_match_its_checksum(hub: Hub, tmp_path: Path) -> None:
    hub.files["onnx/model.onnx"] = b"\x08\x02" * 5000  # same size, other bytes
    with pytest.raises(embeddings.ChecksumMismatch):
        embeddings.fetch(tmp_path, base_url=hub.url)
    directory = embeddings.model_path(tmp_path)
    assert not (directory / "onnx" / "model.onnx").exists()
    assert not list(directory.rglob("*.part"))  # no partial file is left to be used
    hub.files["onnx/model.onnx"] = b"\x08\x01" * 6000  # longer than pinned
    with pytest.raises(embeddings.ChecksumMismatch):
        embeddings.fetch(tmp_path, base_url=hub.url)


def test_cli_fetch_model_exit_codes(
    hub: Hub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CQ_EMBEDDING_MODEL_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as done:
        main.run(["fetch-model", "--base-url", hub.url])
    assert done.value.code == 0
    assert embeddings.HF_REVISION in capsys.readouterr().out
    assert (embeddings.model_path(tmp_path) / "config.json").is_file()

    hub.files["config.json"] = b'{"model": "evil"}'
    other = tmp_path / "other"
    with pytest.raises(SystemExit) as done:
        main.run(["fetch-model", "--dir", str(other), "--base-url", hub.url])
    assert done.value.code == 1
    assert "does not match" in capsys.readouterr().err

    with pytest.raises(SystemExit) as done:  # nothing listens there
        main.run(["fetch-model", "--dir", str(other), "--base-url", "http://127.0.0.1:9"])
    assert done.value.code == 2


def test_local_embedder_reports_a_missing_model(tmp_path: Path) -> None:
    embedder = embeddings.LocalEmbedder(tmp_path)
    assert not embedder.ready
    with pytest.raises(embeddings.ModelUnavailable, match="cq-knowledge fetch-model"):
        embedder.load()
    assert not embedder.ready
    with pytest.raises(PlatformError) as refused:
        embedder.embed_query("x")
    assert refused.value.status_code == 503
    assert refused.value.code == "EMBEDDING_MODEL_UNAVAILABLE"


def test_local_embedder_refuses_altered_files(
    hub: Hub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = embeddings.fetch(tmp_path, base_url=hub.url)
    (directory / "onnx" / "model.onnx").write_bytes(b"\x08\x02" * 5000)
    with pytest.raises(embeddings.ModelUnavailable, match="checksum"):
        embeddings.LocalEmbedder(tmp_path).load()


def test_local_embedder_loads_offline_from_the_pinned_directory(
    hub: Hub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = embeddings.fetch(tmp_path, base_url=hub.url)
    seen: dict[str, object] = {}

    class FakeTextEmbedding:
        def __init__(self, model_name: str, **kwargs: object) -> None:
            import os

            seen.update(kwargs, model_name=model_name, offline=os.environ.get("HF_HUB_OFFLINE"))

    import fastembed

    monkeypatch.setattr(fastembed, "TextEmbedding", FakeTextEmbedding)
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")  # restored after the test
    embedder = embeddings.LocalEmbedder(tmp_path)
    embedder.load()
    assert embedder.ready
    assert seen == {
        "model_name": "jinaai/jina-embeddings-v2-small-en",
        "cache_dir": str(directory),
        "specific_model_path": str(directory),
        "local_files_only": True,
        "offline": "1",
    }
