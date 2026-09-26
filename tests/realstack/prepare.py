"""Build and push the three bundled agent images, then write the catalog the stack loads.

    uv run python tests/realstack/prepare.py --registry localhost:15001
    uv run python tests/realstack/prepare.py --registry localhost:5001 --out .demo --no-test-variants

The second form is ``make demo-up`` (the developer's own stack): no test-only entries, and
no bundled entries whose image digest is a placeholder (``hello-crew``, which only the fake
runtime can "run"), since the real runtime daemon would fail to pull them.

1. ``crewctl build --push`` for each agent in agents/ (host architecture), pinning the
   pushed digest into tests/realstack/.generated/manifests/<agent>.yaml. Committed manifests
   keep their ``@sha256:REQUIRED_DIGEST`` placeholder; digests are machine-specific.
2. tests/realstack/.generated/catalog/ (mounted as the control API's CQ_CATALOG_DIR):
   the bundled catalog/dev/*.yaml, the three pinned agents, and test-only variants of the
   contract probe image (see ``variants``: ``realstack-oom``, ``realstack-crash``).
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
AGENTS = ("contract_probe", "gmail_digest", "caller", "personal_space", "pr_reviewer")

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
# Exits with code 3 before it hands shake, as an agent with a broken import would.
CRASH_PROGRAM = "print('crashing before the handshake',flush=True);raise SystemExit(3)"


def _variant(
    probe: dict[str, Any], agent_id: str, name: str, summary: str, program: str
) -> dict[str, Any]:
    variant = copy.deepcopy(probe)
    variant["metadata"].update(id=agent_id, name=name, summary=summary)
    variant["metadata"].pop("description", None)
    variant["spec"]["entrypoint"] = ["python", "-c", program]
    variant["spec"]["permissions"] = {
        "llmProfiles": [],
        "knowledge": [],
        "connectors": {},
        "cloudProviders": [],
        "userInput": False,
    }
    variant["spec"]["configurationSchema"] = {"type": "object", "properties": {}}
    variant["spec"].pop("resultSchema", None)
    return variant


def variants(probe: dict[str, Any]) -> list[dict[str, Any]]:
    """Test-only catalog entries that reuse the pinned contract probe image."""
    oom = _variant(
        probe,
        "realstack-oom",
        "Realstack OOM",
        "Test only: allocates memory until the container is OOM-killed.",
        OOM_PROGRAM,
    )
    oom["spec"]["resources"]["memoryMb"] = 64
    crash = _variant(
        probe,
        "realstack-crash",
        "Realstack crash",
        "Test only: exits with code 3 before its handshake.",
        CRASH_PROGRAM,
    )
    return [oom, crash]


def placeholder_image(image: str) -> bool:
    """True for an image reference that was never pushed: no ``@sha256:`` digest, a
    non-hex one such as ``REQUIRED_DIGEST``, or an all-zero-ish one such as ``000...01``."""
    _, sep, digest = image.partition("@sha256:")
    if not sep or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        return True
    return len(set(digest)) <= 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default="localhost:15001")
    parser.add_argument("--platform", default=host_platform())
    parser.add_argument("--skip-build", action="store_true", help="reuse <out>/manifests")
    parser.add_argument("--out", type=Path, default=GENERATED, help="output directory")
    parser.add_argument(
        "--no-test-variants", action="store_true", help="omit the realstack-oom/-crash entries"
    )
    args = parser.parse_args()

    out = args.out if args.out.is_absolute() else REPO / args.out
    manifests_dir = out / "manifests"
    catalog_dir = out / "catalog"
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
        image = str((yaml.safe_load(bundled.read_text()).get("spec") or {}).get("image", ""))
        if args.no_test_variants and placeholder_image(image):
            print(f"omitted {bundled.name}: placeholder image {image}", flush=True)
            continue
        shutil.copy(bundled, catalog_dir / bundled.name)
    for agent in AGENTS:
        shutil.copy(manifests_dir / f"{agent}.yaml", catalog_dir / f"{agent}.yaml")
    probe = yaml.safe_load((manifests_dir / "contract_probe.yaml").read_text())
    for variant in [] if args.no_test_variants else variants(probe):
        path = catalog_dir / f"{variant['metadata']['id']}.yaml"
        path.write_text(yaml.safe_dump(variant, sort_keys=False))
    for path in catalog_dir.iterdir():
        path.chmod(0o644)
    catalog_dir.chmod(0o755)
    print(f"catalog: {', '.join(sorted(p.name for p in catalog_dir.iterdir()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
