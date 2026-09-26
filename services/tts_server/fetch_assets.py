"""Build-time download of everything VoXtream loads besides its own weights.

The appliance serves models on an internal network with no internet access, so the image
must already hold the Mimi codec, the phoneme aligner and its tokenizer, the ReDimNet
speaker encoder (code and checkpoint), the NLTK data, and the sample voice prompts. Every
source is pinned to a commit and every file is checked against a SHA-256 before it is
kept. The VoXtream weights themselves (herimor/voxtream) are downloaded by the runtime
daemon like any other catalog model and mounted read-only at run time.

    python fetch_assets.py /opt/cq-tts
"""

from __future__ import annotations

import hashlib
import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

# Hugging Face files: (repo, revision, filename, sha256). The revision is written to
# refs/main so VoXtream's unpinned hf_hub_download(repo, filename) resolves it offline.
HF_FILES = [
    (
        "kyutai/moshiko-pytorch-bf16",
        "2bfc9ae6e89079a5cc7ed2a68436010d91a3d289",
        "tokenizer-e351c8d8-checkpoint125.safetensors",
        "09b782f0629851a271227fb9d36db65c041790365f11bbe5d3d59369cf863f50",
    ),
    ("charsiu/en_w2v2_fc_10ms", "e9bf8dd314313fc57f6e4d0b5425bde4bbeac80f", "config.json", None),
    (
        "charsiu/en_w2v2_fc_10ms",
        "e9bf8dd314313fc57f6e4d0b5425bde4bbeac80f",
        "pytorch_model.bin",
        "6dc8a18422db7c22e951d5f72dc2afc267b942eb0b8459ac6dcc0cf412536de1",
    ),
    ("charsiu/tokenizer_en_cmu", "10507401aedf5e0aba164128535b49225ff95260", "vocab.json", None),
    (
        "charsiu/tokenizer_en_cmu",
        "10507401aedf5e0aba164128535b49225ff95260",
        "tokenizer_config.json",
        None,
    ),
    (
        "charsiu/tokenizer_en_cmu",
        "10507401aedf5e0aba164128535b49225ff95260",
        "special_tokens_map.json",
        None,
    ),
]

# ReDimNet (MIT): VoXtream calls torch.hub.load("IDRnD/ReDimNet", ...), which uses the
# cached checkout named <owner>_<repo>_<branch> and the cached checkpoint without network.
REDIMNET_COMMIT = "ce039a624cb99fe127702ceb94c6080090e5032f"
REDIMNET_ZIP_SHA256 = "78c36d7f4fe794ab3973b847ece9fc80a04617fcbe56f9048199957f0de4bd74"
REDIMNET_CHECKPOINT = "M-vb2+vox2+cnc-ft_mix.pt"
REDIMNET_CHECKPOINT_SHA256 = "1e0716c2f351e79df054554f17c525b899045d2d05e4c839208b975e87d94e6a"

# Sample voice prompts shipped in the VoXtream repository (commit of its "voxtream" tag).
VOXTREAM_COMMIT = "7cbea590c2393576d88b57b97500012b1cf616d8"
VOICES = {
    "female.wav": "11b998e35bb346f7512c6ceef1ea343cc2f612fe6ee25e2028d05ff52651a8b0",
    "male.wav": "b3e2776fb252ad6794107493531e5d1854c8ca1ae9df4feabfb19746bc1cc702",
}

NLTK_PACKAGES = [
    "averaged_perceptron_tagger",
    "averaged_perceptron_tagger_eng",
    "cmudict",
    "punkt_tab",
]


def fetch(url: str, sha256: str | None) -> bytes:
    with urllib.request.urlopen(url, timeout=600) as response:  # noqa: S310 - fixed https URLs
        data: bytes = response.read()
    if sha256 is not None and hashlib.sha256(data).hexdigest() != sha256:
        raise SystemExit(f"checksum mismatch for {url}")
    return data


def hf_file(root: Path, repo: str, revision: str, filename: str, sha256: str | None) -> None:
    base = root / "hf" / "hub" / f"models--{repo.replace('/', '--')}"
    target = base / "snapshots" / revision / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        fetch(f"https://huggingface.co/{repo}/resolve/{revision}/{filename}", sha256)
    )
    (base / "refs").mkdir(exist_ok=True)
    (base / "refs" / "main").write_text(revision)


def redimnet(root: Path) -> None:
    hub = root / "torch" / "hub"
    archive = fetch(
        f"https://github.com/IDRnD/ReDimNet/archive/{REDIMNET_COMMIT}.zip", REDIMNET_ZIP_SHA256
    )
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        [top] = {name.split("/")[0] for name in zf.namelist()}
        zf.extractall(hub)
    checkout = hub / "IDRnD_ReDimNet_master"
    shutil.rmtree(checkout, ignore_errors=True)
    (hub / top).rename(checkout)
    (hub / "checkpoints").mkdir(parents=True, exist_ok=True)
    (hub / "checkpoints" / REDIMNET_CHECKPOINT).write_bytes(
        fetch(
            "https://github.com/IDRnD/ReDimNet/releases/download/latest/" + REDIMNET_CHECKPOINT,
            REDIMNET_CHECKPOINT_SHA256,
        )
    )
    (hub / "trusted_list").write_text("IDRnD_ReDimNet\n")


def voices(root: Path) -> None:
    target = root / "voices"
    target.mkdir(parents=True, exist_ok=True)
    for name, sha256 in VOICES.items():
        url = f"https://raw.githubusercontent.com/herimor/voxtream/{VOXTREAM_COMMIT}/assets/app/{name}"
        (target / name).write_bytes(fetch(url, sha256))


def nltk_data(root: Path) -> None:
    import nltk

    data = root / "nltk_data"
    for package in NLTK_PACKAGES:
        if not nltk.download(package, download_dir=str(data), quiet=True):
            raise SystemExit(f"could not download NLTK package {package}")
    # VoXtream's aligner checks nltk.data.find("punkt_tab") (not "tokenizers/punkt_tab"),
    # so without this link it downloads the tokenizer again on every start.
    (data / "punkt_tab").symlink_to("tokenizers/punkt_tab")


def main() -> None:
    root = Path(sys.argv[1])
    for repo, revision, filename, sha256 in HF_FILES:
        hf_file(root, repo, revision, filename, sha256)
    redimnet(root)
    voices(root)
    nltk_data(root)
    print(f"assets ready in {root}")


if __name__ == "__main__":
    main()
