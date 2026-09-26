"""Fail when a release references an image by a mutable tag (PLAN.md section 16.2:
"No mutable image tags in release manifests").

    python infra/scripts/check_release_refs.py --mode release [--env-file FILE ...]
    python infra/scripts/check_release_refs.py --mode dev

Checked references:

* every ``image:`` of ``infra/compose/compose.appliance.yaml`` (all profiles), rendered by
  ``docker compose config`` with the given env files, so the release's
  ``CQ_PLATFORM_IMAGE`` / ``CQ_PROXY_IMAGE`` / ``CQ_VERSION`` values are what is checked
  (a release sets e.g. ``CQ_VERSION=0.1.0@sha256:<digest>``);
* ``launch.image`` of every model profile in ``catalog/models/dgx`` and ``catalog/models/dev``;
* ``spec.image`` of every bundled agent manifest in ``catalog/``;
* ``FROM`` lines of the Dockerfiles that build shipped images.

A reference passes when it carries ``@sha256:<64 hex>``. In ``release`` mode any other
reference fails the check; in ``dev`` mode it is printed as a warning.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "infra/compose/compose.appliance.yaml"
DOCKERFILES = [
    "infra/docker/python.Dockerfile",
    "infra/docker/proxy.Dockerfile",
    "agents/contract_probe/Dockerfile",
    "agents/gmail_digest/Dockerfile",
    "agents/caller/Dockerfile",
    "agents/pr_reviewer/Dockerfile",
]
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
# Placeholder values that let `docker compose config` render without real secrets.
PLACEHOLDER_ENV = {
    "CQ_VERSION": "0.0.0-unset",
    "CQ_SOCKET_GID": "1",
    "POSTGRES_PASSWORD": "x",
    "CQ_SECRET_KEY": "x",
    "CQ_CAPABILITY_SIGNING_KEY": "x",
    "CQ_INTERNAL_SERVICE_TOKEN": "x",
    "CQ_CHAT_CLIENT_TOKEN": "x",
    "CQ_VOICE_CLIENT_TOKEN": "x",
}


def pinned(ref: str) -> bool:
    return bool(DIGEST.search(ref.strip()))


def compose_refs(env_files: list[Path]) -> list[tuple[str, str]]:
    """(where, image) for every service of the appliance Compose file, all profiles."""
    docker = shutil.which("docker")
    if docker is None:
        raise SystemExit("docker (with the compose plugin) is required to render the stack")
    env = {**PLACEHOLDER_ENV, **os.environ}
    command = [docker, "compose", "-f", str(COMPOSE)]
    for env_file in env_files:
        command += ["--env-file", str(env_file)]
        env = {k: v for k, v in env.items() if k not in _keys(env_file)}
    command += ["--profile", "*", "config", "--format", "json"]
    rendered = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        command, check=True, capture_output=True, text=True, env=env
    )
    services: dict[str, Any] = json.loads(rendered.stdout)["services"]
    return [(f"compose service {name}", str(s.get("image", ""))) for name, s in services.items()]


def _keys(env_file: Path) -> set[str]:
    return {
        line.split("=", 1)[0].strip()
        for line in env_file.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }


def catalog_refs() -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for profile in sorted((ROOT / "catalog/models").glob("*/*.json")):
        image = json.loads(profile.read_text()).get("launch", {}).get("image", "")
        refs.append((f"model profile {profile.relative_to(ROOT)}", image))
    for manifest in sorted((ROOT / "catalog").glob("**/*.yaml")):
        data = yaml.safe_load(manifest.read_text())
        if isinstance(data, dict) and isinstance(data.get("spec"), dict):
            refs.append((f"agent manifest {manifest.relative_to(ROOT)}", data["spec"]["image"]))
    return refs


def dockerfile_refs() -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for name in DOCKERFILES:
        stages: set[str] = set()
        for line in (ROOT / name).read_text().splitlines():
            parts = line.split()
            if parts[:1] != ["FROM"] and parts[:1] != ["from"]:
                continue
            args = [p for p in parts[1:] if not p.startswith("--")]
            if not args:
                continue
            image = args[0]
            if len(args) >= 3 and args[1].lower() == "as":
                stages.add(args[2])
            if image not in stages:
                refs.append((f"{name} FROM", image))
        for line in (ROOT / name).read_text().splitlines():  # COPY --from=image:tag
            match = re.search(r"COPY\s+--from=(\S+/\S+)", line)
            if match:
                refs.append((f"{name} COPY --from", match.group(1)))
    return refs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mode", choices=["release", "dev"], default="dev")
    parser.add_argument("--env-file", type=Path, action="append", default=[])
    parser.add_argument(
        "--skip-compose", action="store_true", help="skip the Compose file (no docker)"
    )
    parser.add_argument(
        "--skip-dockerfiles", action="store_true", help="check only what the appliance pulls"
    )
    args = parser.parse_args(argv)
    refs = catalog_refs() + ([] if args.skip_dockerfiles else dockerfile_refs())
    if not args.skip_compose:
        refs = compose_refs(args.env_file) + refs
    unpinned = [(where, ref) for where, ref in refs if not pinned(ref)]
    for where, ref in refs:
        print(f"{'ok  ' if pinned(ref) else 'TAG '} {where}: {ref}")
    if not unpinned:
        print(f"All {len(refs)} image references are pinned by digest.")
        return 0
    label = "ERROR" if args.mode == "release" else "WARNING"
    print(f"{label}: {len(unpinned)} of {len(refs)} image references use a mutable tag.")
    return 1 if args.mode == "release" else 0


if __name__ == "__main__":
    sys.exit(main())
