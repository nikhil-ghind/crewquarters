"""Build and push the three bundled agent images, then write the catalog the stack loads.

    uv run python tests/realstack/prepare.py --registry localhost:15001

1. ``crewctl build --push`` for each agent in agents/ (host architecture), pinning the
   pushed digest into tests/realstack/.generated/manifests/<agent>.yaml. Committed manifests
   keep their ``@sha256:REQUIRED_DIGEST`` placeholder; digests are machine-specific.
2. tests/realstack/.generated/catalog/ (mounted as the control API's CQ_CATALOG_DIR):
   the bundled catalog/dev/*.yaml, the three pinned agents, and test-only variants of the
   contract probe image (see ``VARIANTS``).
"""

from __future__ import annotations

import argparse
import copy
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

from crewctl.build import build, host_platform

REPO = Path(__file__).resolve().parents[2]
GENERATED = REPO / "tests" / "realstack" / ".generated"
AGENTS = ("contract_probe", "gmail_digest", "caller")

# A tiny agent, run from the contract probe image with the manifest's entrypoint: it hands
# shakes, logs, then allocates memory until the container's cgroup limit kills it.
OOM_PROGRAM = (
    "import asyncio,crewquarters as c\n"
    "a=c.Agent('oom')\n"
    "@a.run\n"
    "async def r(ctx):\n"
    " await ctx.events.log('info','allocating');await ctx.events.flush();b=[]\n"
    " while 1:b.append(b'x'*(8<<20));await asyncio.sleep(.05)\n"
    "a.serve()"
)


def variants(probe: dict[str, Any]) -> list[dict[str, Any]]:
    """Test-only catalog entries that reuse the pinned contract probe image."""
    oom = copy.deepcopy(probe)
    oom["metadata"].update(
        id="realstack-oom",
        name="Realstack OOM",
        summary="Test only: allocates memory until the container is OOM-killed.",
    )
    oom["metadata"].pop("description", None)
    oom["spec"]["entrypoint"] = ["python", "-c", OOM_PROGRAM]
    oom["spec"]["resources"]["memoryMb"] = 64
    oom["spec"]["permissions"] = {
        "llmProfiles": [],
        "knowledge": [],
        "connectors": {},
        "cloudProviders": [],
        "userInput": False,
    }
    oom["spec"]["configurationSchema"] = {"type": "object", "properties": {}}
    oom["spec"].pop("resultSchema", None)
    return [oom]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default="localhost:15001")
    parser.add_argument("--platform", default=host_platform())
    parser.add_argument("--skip-build", action="store_true", help="reuse .generated/manifests")
    args = parser.parse_args()

    manifests_dir = GENERATED / "manifests"
    catalog_dir = GENERATED / "catalog"
    if not args.skip_build:
        for agent in AGENTS:
            result = build(
                REPO / "agents" / agent,
                platforms=args.platform,
                push=True,
                registry=args.registry,
                output_manifest=manifests_dir / f"{agent}.yaml",
            )
            print(f"{agent}: {result.pinned_image}", flush=True)

    shutil.rmtree(catalog_dir, ignore_errors=True)
    catalog_dir.mkdir(parents=True)
    for bundled in sorted((REPO / "catalog" / "dev").glob("*.yaml")):
        shutil.copy(bundled, catalog_dir / bundled.name)
    for agent in AGENTS:
        shutil.copy(manifests_dir / f"{agent}.yaml", catalog_dir / f"{agent}.yaml")
    probe = yaml.safe_load((manifests_dir / "contract_probe.yaml").read_text())
    for variant in variants(probe):
        path = catalog_dir / f"{variant['metadata']['id']}.yaml"
        path.write_text(yaml.safe_dump(variant, sort_keys=False))
    for path in catalog_dir.iterdir():
        path.chmod(0o644)
    catalog_dir.chmod(0o755)
    print(f"catalog: {', '.join(sorted(p.name for p in catalog_dir.iterdir()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
