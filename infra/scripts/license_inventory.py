"""Generate the third-party license inventory (PLAN.md section 23.1).

    uv run python infra/scripts/license_inventory.py            # write THIRD_PARTY_LICENSES.md
    uv run python infra/scripts/license_inventory.py --check    # fail if it is out of date

Sources:

* Python: ``uv.lock``. Only what ships is listed: the dependency closure of the platform
  image (the root project's dependencies, installed without dev groups) and of the agent
  images (the SDK and the three agents). License names come from each installed
  distribution's metadata, so run it after ``uv sync --all-packages``.
* npm: ``apps/web/package-lock.json`` production packages (``dev`` is not set), which are
  what Vite bundles into the UI. Bundled dependencies (``inBundle``) are included.
* Base images, bundled services, and models: the pinned references in the Dockerfiles,
  ``infra/compose/compose.appliance.yaml``, ``catalog/models/**``, and the knowledge
  service's embedding model, with licenses from :data:`COMPONENTS` (verified by hand
  against the upstream model cards / project licenses on the date in each note).

Anything whose license is copyleft (GPL/LGPL/AGPL/SSPL/EUPL/MPL...), proprietary, or unknown
is listed under "Needs review".
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "THIRD_PARTY_LICENSES.md"

# Workspace members whose dependency closures ship in an image.
PLATFORM_ROOTS = [
    "crewquarters-shared",
    "crewquarters-control-api",
    "crewquarters-scheduler",
    "crewquarters-runtime",
    "crewquarters-gateway",
    "crewquarters-secret-store",
    "crewquarters-capability-broker",
    "crewquarters-knowledge",
]
AGENT_ROOTS = [
    "crewquarters-sdk",
    "crewquarters-agent-contract-probe",
    "crewquarters-agent-gmail-digest",
    "crewquarters-agent-caller",
    "crewquarters-agent-personal-space",
]

REVIEW = re.compile(r"GPL|SSPL|EUPL|MPL|CDDL|EPL|OSL|CC-BY-SA|Commons Clause|BUSL|Proprietary|"
                    r"NVIDIA|UNKNOWN|Other", re.IGNORECASE)  # fmt: skip

# Classifier and free-text license names mapped to SPDX identifiers.
_SPDX = {
    "MIT License": "MIT",
    "MIT": "MIT",
    "BSD License": "BSD",
    "Apache Software License": "Apache-2.0",
    "Apache 2.0": "Apache-2.0",
    "Apache License 2.0": "Apache-2.0",
    "Apache License, Version 2.0": "Apache-2.0",
    "Apache-2.0": "Apache-2.0",
    "Python Software Foundation License": "PSF-2.0",
    "ISC License (ISCL)": "ISC",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0",
    "GNU Library or Lesser General Public License (LGPL)": "LGPL",
    "GNU General Public License v2 (GPLv2)": "GPL-2.0",
    "The Unlicense (Unlicense)": "Unlicense",
    "3-Clause BSD License": "BSD-3-Clause",
    "BSD-3-Clause": "BSD-3-Clause",
    "BSD 3-Clause": "BSD-3-Clause",
    "new BSD": "BSD-3-Clause",
}


# Packages whose wheels carry no license metadata, checked by hand against upstream.
OVERRIDES = {
    "py-rust-stemmers": "MIT (no wheel metadata; github.com/qdrant/py-rust-stemmers, 2026-09-25)",
}


@dataclass(frozen=True)
class Entry:
    name: str
    version: str
    license: str
    where: str
    note: str = ""


@dataclass(frozen=True)
class Component:
    name: str
    reference: str
    license: str
    where: str
    note: str


def _base_image(dockerfile: str, match: str) -> str:
    for line in (ROOT / dockerfile).read_text().splitlines():
        if line.startswith("FROM") and match in line:
            return next(p for p in line.split()[1:] if not p.startswith("--"))
    return "(not found)"


def _compose_image(prefix: str) -> str:
    text = (ROOT / "infra/compose/compose.appliance.yaml").read_text()
    found = re.search(rf"image:\s*({re.escape(prefix)}\S*)", text)
    return found.group(1) if found else "(not found)"


def components() -> list[Component]:
    """Non-package components that ship in, or are downloaded by, the appliance."""
    out = [
        Component(
            "Python 3.12 on Debian 12 (python:3.12-slim-bookworm)",
            _base_image("infra/docker/python.Dockerfile", "python:"),
            "PSF-2.0 (CPython); Debian packages under their own licenses (GPL-2.0/GPL-3.0/"
            "LGPL-2.1/BSD/MIT...)",
            "platform image base",
            "OS packages are unmodified Debian binaries (mere aggregation); per-package "
            "licenses are in each image's SPDX SBOM and /usr/share/doc/*/copyright.",
        ),
        Component(
            "Python 3.12 on Debian 12 (python:3.12-slim)",
            _base_image("agents/gmail_digest/Dockerfile", "python:"),
            "PSF-2.0 (CPython); Debian packages under their own licenses",
            "agent images base",
            "As above.",
        ),
        Component(
            "NGINX (nginxinc/nginx-unprivileged, Alpine)",
            _base_image("infra/docker/proxy.Dockerfile", "nginx"),
            "BSD-2-Clause (nginx); Alpine packages under their own licenses (musl: MIT, "
            "busybox: GPL-2.0...)",
            "proxy image base",
            "Unmodified upstream binaries; per-package licenses in the proxy SBOM.",
        ),
        Component(
            "Node.js (node:22 bookworm-slim)",
            _base_image("infra/docker/proxy.Dockerfile", "node:"),
            "MIT (Node.js)",
            "build stage only",
            "Builds the UI; not present in any shipped image.",
        ),
        Component(
            "uv",
            "ghcr.io/astral-sh/uv:0.5.11",
            "Apache-2.0 OR MIT",
            "build stage only",
            "Installs the platform's Python packages; not in the final image.",
        ),
        Component(
            "PostgreSQL 16 with pgvector (pgvector/pgvector)",
            _compose_image("pgvector/pgvector"),
            "PostgreSQL (PostgreSQL and pgvector); Debian packages under their own licenses",
            "appliance database (pulled, unmodified)",
            "pgvector is under the PostgreSQL License (github.com/pgvector/pgvector/LICENSE).",
        ),
        Component(
            "cloudflared",
            _compose_image("cloudflare/cloudflared"),
            "Apache-2.0",
            "optional `callbacks` profile (pulled, unmodified)",
            "github.com/cloudflare/cloudflared/LICENSE.",
        ),
    ]
    vllm_images = sorted(
        {
            json.loads(p.read_text())["launch"]["image"]
            for p in (ROOT / "catalog/models/dgx").glob("*.json")
        }
    )
    for image in vllm_images:
        out.append(
            Component(
                "NVIDIA vLLM container (NGC)",
                image,
                "NVIDIA Deep Learning Container License (proprietary; vLLM itself Apache-2.0)",
                "model serving on GB10 (pulled from nvcr.io, unmodified; bundled only with "
                "build-offline-bundle.sh --with-vllm)",
                "Redistribution in an offline bundle is governed by NVIDIA's container "
                "license; review before shipping a bundle that includes it.",
            )
        )
    for image in sorted(
        {
            json.loads(p.read_text())["launch"]["image"]
            for p in (ROOT / "catalog/models/dev").glob("*.json")
        }
    ):
        out.append(
            Component(
                "Python (mock model server base, dev/CI only)",
                image,
                "PSF-2.0; Debian packages under their own licenses",
                "laptop/CI mock model profiles",
                "Not used on the appliance.",
            )
        )
    return out


def models() -> list[Component]:
    out: list[Component] = []
    for profile in sorted((ROOT / "catalog/models").glob("*/*.json")):
        data = json.loads(profile.read_text())
        source = data["source"]
        if source["type"] != "huggingface":
            continue
        license_ = data["license"]
        out.append(
            Component(
                data["displayName"],
                f"huggingface.co/{source['repo']}@{source['revision']}",
                license_["name"],
                f"model catalog ({profile.relative_to(ROOT)}), downloaded on the appliance",
                f"Model card license verified as {license_['name']} (2026-09-25); gated: "
                f"{str(license_['gated']).lower()}.",
            )
        )
    embeddings = (ROOT / "services/knowledge/src/crewquarters_knowledge/embeddings.py").read_text()
    repo = re.search(r'HF_REPO = "([^"]+)"', embeddings)
    revision = re.search(r'HF_REVISION = "([^"]+)"', embeddings)
    out.append(
        Component(
            "jina-embeddings-v2-small-en (ONNX export)",
            f"huggingface.co/{repo.group(1) if repo else '?'}"
            f"@{revision.group(1) if revision else '?'}",
            "Apache-2.0 (inherited from jinaai/jina-embeddings-v2-small-en)",
            "knowledge service embedding model (downloaded or in the offline bundle)",
            "The Xenova repository declares no license of its own; it is a format conversion "
            "of jinaai/jina-embeddings-v2-small-en, whose model card is Apache-2.0 "
            "(verified 2026-09-25). Keep the upstream attribution in NOTICE.",
        )
    )
    return out


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _marker_ok(marker: str | None) -> bool:
    if not marker:
        return True
    try:
        from packaging.markers import Marker
    except ImportError:  # pragma: no cover - packaging ships with every venv tool
        return True
    env = {
        "sys_platform": "linux",
        "platform_system": "Linux",
        "os_name": "posix",
        "python_version": "3.12",
        "python_full_version": "3.12.8",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "platform_machine": "x86_64",
    }
    arm = {**env, "platform_machine": "aarch64"}
    parsed = Marker(marker)
    return bool(parsed.evaluate(env) or parsed.evaluate(arm))


def closure(lock: dict[str, Any], roots: list[str]) -> set[str]:
    packages = {_normalize(p["name"]): p for p in lock["package"]}
    visited: set[tuple[str, tuple[str, ...]]] = set()
    stack: list[tuple[str, tuple[str, ...]]] = [(_normalize(r), ()) for r in roots]
    while stack:
        name, extras = stack.pop()
        if (name, extras) in visited:
            continue
        visited.add((name, extras))
        package = packages[name]
        deps = list(package.get("dependencies", []))
        for extra in extras:
            deps += package.get("optional-dependencies", {}).get(extra, [])
        for dep in deps:
            if _marker_ok(dep.get("marker")):
                stack.append((_normalize(dep["name"]), tuple(dep.get("extra", ()))))
    return {name for name, _ in visited}


def _license_of(name: str) -> str:
    if name in OVERRIDES:
        return OVERRIDES[name]
    try:
        meta = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        return "UNKNOWN (not installed: run uv sync --all-packages)"
    expression = meta.get("License-Expression")
    if expression:
        return str(expression)
    classifiers = [
        c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License ::")
    ]
    names = sorted({_SPDX.get(c, c) for c in classifiers if c != "OSI Approved"})
    free_text = (meta.get("License") or "").strip()
    if free_text and len(free_text) < 60 and "\n" not in free_text:
        text_name = _SPDX.get(free_text, free_text)
        if not names or (text_name not in names and "BSD" not in names):
            names = sorted({*names, text_name})
    if not names and free_text:
        first = free_text.splitlines()[0][:60]
        names = [_guess(free_text) or f"see package ({first})"]
    return " AND ".join(names) if names else "UNKNOWN"


def _guess(text: str) -> str | None:
    lowered = text.lower()
    for needle, spdx in (
        ("apache license", "Apache-2.0"),
        ("mit license", "MIT"),
        ("permission is hereby granted, free of charge", "MIT"),
        ("redistribution and use in source and binary forms", "BSD"),
        ("python software foundation", "PSF-2.0"),
        ("mozilla public license", "MPL-2.0"),
    ):
        if needle in lowered:
            return spdx
    return None


def python_entries() -> list[Entry]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    by_name = {_normalize(p["name"]): p for p in lock["package"]}
    platform = closure(lock, PLATFORM_ROOTS)
    agents = closure(lock, AGENT_ROOTS)
    entries: list[Entry] = []
    for name in sorted(platform | agents):
        package = by_name[name]
        if "registry" not in package.get("source", {}):
            continue  # workspace members are Crewquarters' own code
        where = ", ".join(
            label for label, members in (("platform", platform), ("agents", agents))
            if name in members
        )  # fmt: skip
        entries.append(Entry(package["name"], package["version"], _license_of(name), where))
    return entries


def npm_entries() -> list[Entry]:
    lock = json.loads((ROOT / "apps/web/package-lock.json").read_text())
    entries: list[Entry] = []
    for path, info in sorted(lock["packages"].items()):
        if not path or info.get("dev") or info.get("devOptional") or info.get("link"):
            continue
        name = path.split("node_modules/")[-1]
        where = "web UI bundle" + (" (bundled dependency)" if info.get("inBundle") else "")
        entries.append(
            Entry(name, str(info.get("version", "?")), str(info.get("license", "UNKNOWN")), where)
        )
    return entries


def needs_review(license_: str) -> bool:
    return bool(REVIEW.search(license_))


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    def cell(value: str) -> str:
        return value.replace("|", "\\|").replace("\n", " ")

    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return out


def render() -> str:
    python = python_entries()
    npm = npm_entries()
    comps = components()
    mods = models()
    review: list[list[str]] = []
    for e in python + npm:
        if needs_review(e.license):
            review.append([f"{e.name} {e.version}", e.license, e.where])
    for c in comps + mods:
        if needs_review(c.license) or "declares no license" in c.note:
            review.append([c.name, c.license, c.note])
    lines = [
        "# Third-party licenses",
        "",
        "Generated by `infra/scripts/license_inventory.py` from `uv.lock`,",
        "`apps/web/package-lock.json`, the Dockerfiles, `infra/compose/compose.appliance.yaml`,",
        "and `catalog/models/`. Do not edit by hand: regenerate with",
        "`uv run python infra/scripts/license_inventory.py` (CI fails when it is stale).",
        "Installed by the `.deb` as `/usr/share/doc/crewquarters/THIRD_PARTY_LICENSES.md`.",
        "",
        "Per-file licenses of OS packages inside each image are in that image's SPDX SBOM",
        "(CI artifact `sbom-<image>-<arch>`).",
        "",
        "## Needs review",
        "",
        "Copyleft, proprietary, or unclear licenses. Each needs a recorded decision in",
        "`docs/security/release-checklist.md` before a release.",
        "",
        *_table(["Component", "License", "Why"], review),
        "",
        "## Models",
        "",
        *_table(
            ["Model", "Pinned reference", "License", "Used by", "Notes"],
            [[m.name, f"`{m.reference}`", m.license, m.where, m.note] for m in mods],
        ),
        "",
        "## Images and bundled services",
        "",
        *_table(
            ["Component", "Pinned reference", "License", "Used by", "Notes"],
            [[c.name, f"`{c.reference}`", c.license, c.where, c.note] for c in comps],
        ),
        "",
        f"## Python packages ({len(python)})",
        "",
        "The runtime closure of the platform image and the agent images (dev tools excluded),",
        "at the versions in `uv.lock`. The agent images install the SDK with `pip` from package",
        "metadata, so their exact installed versions are recorded in their SBOMs.",
        "",
        *_table(
            ["Package", "Version", "License", "Ships in"],
            [[e.name, e.version, e.license, e.where] for e in python],
        ),
        "",
        f"## npm packages ({len(npm)})",
        "",
        "Production dependencies of `apps/web`, bundled into the UI served by the proxy.",
        "",
        *_table(
            ["Package", "Version", "License", "Ships in"],
            [[e.name, e.version, e.license, e.where] for e in npm],
        ),
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if the file is stale")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        current = args.output.read_text() if args.output.exists() else ""
        if current != text:
            sys.stdout.writelines(
                difflib.unified_diff(
                    current.splitlines(keepends=True),
                    text.splitlines(keepends=True),
                    str(args.output),
                    "regenerated",
                )
            )
            print(
                "\nTHIRD_PARTY_LICENSES.md is stale: run "
                "`uv run python infra/scripts/license_inventory.py` and commit it."
            )
            return 1
        print("THIRD_PARTY_LICENSES.md is up to date.")
        return 0
    args.output.write_text(text)
    print(f"wrote {args.output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
