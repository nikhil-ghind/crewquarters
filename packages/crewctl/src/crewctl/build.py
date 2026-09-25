"""`crewctl build`: docker buildx build, optional push, and digest pinning in manifest.yaml."""

from __future__ import annotations

import json
import platform
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crewquarters_fake.contracts import load_manifest

Runner = Callable[[list[str]], Any]
_IMAGE_LINE_RE = re.compile(r"^(?P<prefix>\s*image:\s*)(?P<value>\S+)", re.MULTILINE)


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "packages" / "python_sdk" / "pyproject.toml").is_file():
            return candidate
    raise FileNotFoundError(f"no Crewquarters repository above {start}")


def image_repo(registry: str, agent_id: str) -> str:
    return f"{registry.rstrip('/')}/crewquarters/{agent_id}"


def rewrite_image_line(text: str, new_image: str) -> str:
    match = _IMAGE_LINE_RE.search(text)
    if match is None:
        raise ValueError("manifest has no image: line to pin")
    return text[: match.start("value")] + new_image + text[match.end("value") :]


def host_platform() -> str:
    return "linux/arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "linux/amd64"


def build_command(
    *,
    context: Path,
    dockerfile: Path,
    tag: str,
    platforms: str,
    push: bool,
    metadata_file: Path | None,
) -> list[str]:
    command = [
        "docker",
        "buildx",
        "build",
        "--platform",
        platforms,
        "-f",
        str(dockerfile),
        "-t",
        tag,
    ]
    command.append("--push" if push else "--load")
    if metadata_file is not None:
        command += ["--metadata-file", str(metadata_file)]
    return [*command, str(context)]


@dataclass(frozen=True)
class BuildResult:
    tag: str
    platforms: str
    digest: str | None
    pinned_image: str | None


def _run(command: list[str]) -> Any:
    return subprocess.run(command, check=True)


def build(
    agent_dir: Path,
    *,
    platforms: str | None = None,
    push: bool = False,
    registry: str = "localhost:5001",
    runner: Runner = _run,
    output_manifest: Path | None = None,
) -> BuildResult:
    agent_dir = agent_dir.resolve()
    manifest_file = agent_dir / "manifest.yaml"
    manifest = load_manifest(manifest_file)
    agent_id, version = manifest["metadata"]["id"], manifest["metadata"]["version"]
    repo = image_repo(registry, agent_id)
    tag = f"{repo}:{version}"
    chosen = platforms or ("linux/amd64,linux/arm64" if push else host_platform())
    context = find_repo_root(agent_dir)
    with tempfile.TemporaryDirectory(prefix="crewctl-build-") as scratch:
        metadata = Path(scratch) / "metadata.json" if push else None
        runner(
            build_command(
                context=context,
                dockerfile=agent_dir / "Dockerfile",
                tag=tag,
                platforms=chosen,
                push=push,
                metadata_file=metadata,
            )
        )
        if metadata is None:
            return BuildResult(tag, chosen, None, None)
        digest = str(json.loads(metadata.read_text())["containerimage.digest"])
    pinned = f"{repo}@{digest}"
    target = output_manifest or manifest_file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rewrite_image_line(manifest_file.read_text(), pinned), encoding="utf-8")
    return BuildResult(tag, chosen, digest, pinned)
