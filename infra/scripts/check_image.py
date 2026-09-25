"""Check a built image archive without running it (PLAN.md section 23.6).

    python infra/scripts/check_image.py IMAGE.tar [--allowlist infra/scripts/setuid-allowlist.txt]

``IMAGE.tar`` is a ``docker save`` / ``buildx --output type=docker`` archive, or an OCI
layout archive, of one platform's image. Nothing is executed, so an ``arm64`` image is
checked on an ``amd64`` runner exactly like a native one.

Checks:

* the image's ``Config.User`` is set and is not root (``0``, ``root``, ``0:0``...);
* the final filesystem (layers applied in order, whiteouts honoured) has no setuid or
  setgid file except those listed in the allowlist (one absolute path per line, ``#``
  comments allowed).

Exit status 0 when both pass, 1 otherwise; findings are printed one per line.
"""

from __future__ import annotations

import argparse
import io
import json
import posixpath
import stat
import sys
import tarfile
from pathlib import Path
from typing import IO, Any

ROOT_USERS = {"", "0", "root"}
DEFAULT_ALLOWLIST = Path(__file__).with_name("setuid-allowlist.txt")


def _read_json(archive: tarfile.TarFile, name: str) -> Any:
    member = archive.extractfile(name)
    if member is None:
        raise ValueError(f"{name} is not a file in the archive")
    return json.load(member)


def _blob(digest: str) -> str:
    algorithm, value = digest.split(":", 1)
    return f"blobs/{algorithm}/{value}"


def image_parts(archive: tarfile.TarFile) -> tuple[dict[str, Any], list[str]]:
    """The image config and its layer paths inside the archive, base layer first."""
    names = set(archive.getnames())
    if "manifest.json" in names:  # docker save format
        manifest = _read_json(archive, "manifest.json")
        if len(manifest) != 1:
            raise ValueError("the archive must hold exactly one image")
        return _read_json(archive, manifest[0]["Config"]), list(manifest[0]["Layers"])
    index = _read_json(archive, "index.json")  # OCI layout
    descriptor = index["manifests"][0]
    node = _read_json(archive, _blob(descriptor["digest"]))
    while "manifests" in node:  # an image index: take its only (or first non-attestation) image
        images = [
            m
            for m in node["manifests"]
            if m.get("annotations", {}).get("vnd.docker.reference.type") != "attestation-manifest"
        ]
        node = _read_json(archive, _blob(images[0]["digest"]))
    config = _read_json(archive, _blob(node["config"]["digest"]))
    return config, [_blob(layer["digest"]) for layer in node["layers"]]


def _open_layer(archive: tarfile.TarFile, name: str) -> tarfile.TarFile:
    raw = archive.extractfile(name)
    if raw is None:
        raise ValueError(f"layer {name} is missing")
    data: IO[bytes] = io.BytesIO(raw.read())
    return tarfile.open(fileobj=data, mode="r:*")


def privileged_files(archive: tarfile.TarFile, layers: list[str]) -> dict[str, int]:
    """Setuid/setgid regular files in the final filesystem: path -> mode."""
    present: dict[str, int] = {}
    for layer_name in layers:
        with _open_layer(archive, layer_name) as layer:
            for member in layer:
                path = "/" + posixpath.normpath(member.name.lstrip("./")).lstrip("/")
                directory, base = posixpath.split(path)
                if base == ".wh..wh..opq":  # opaque directory: hide everything below it
                    prefix = directory.rstrip("/") + "/"
                    for known in [p for p in present if p.startswith(prefix)]:
                        del present[known]
                    continue
                if base.startswith(".wh."):
                    hidden = posixpath.join(directory, base[4:])
                    for known in [p for p in present if p == hidden or p.startswith(hidden + "/")]:
                        del present[known]
                    continue
                present.pop(path, None)
                if member.isfile() and member.mode & (stat.S_ISUID | stat.S_ISGID):
                    present[path] = member.mode
    return present


def load_allowlist(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        line.split("#", 1)[0].strip()
        for line in path.read_text().splitlines()
        if line.split("#", 1)[0].strip()
    }


def is_root(user: str) -> bool:
    name = user.split(":", 1)[0].strip()
    return name in ROOT_USERS


def check(image: Path, allowlist: set[str]) -> list[str]:
    findings: list[str] = []
    with tarfile.open(image) as archive:
        config, layers = image_parts(archive)
        user = str((config.get("config") or {}).get("User") or "")
        if is_root(user):
            findings.append(f"runs as root: Config.User is {user!r}")
        for path, mode in sorted(privileged_files(archive, layers).items()):
            if path not in allowlist:
                kind = "setuid" if mode & stat.S_ISUID else "setgid"
                findings.append(f"{kind} file not in the allowlist: {path} ({oct(mode)})")
        arch = config.get("architecture", "?")
        print(f"{image.name}: {arch}, user {user or '(unset)'}, {len(layers)} layers")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    args = parser.parse_args(argv)
    allowlist = load_allowlist(args.allowlist)
    failed = False
    for image in args.images:
        for finding in check(image, allowlist):
            print(f"  FAIL {finding}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
