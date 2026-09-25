"""Write an acceptance-evidence report (Markdown) from JUnit results and environment facts."""

from __future__ import annotations

import argparse
import platform
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCKER_FORMAT = "{{.Server.Version}} {{.Server.Arch}}"
COUNT_KEYS = ("tests", "failures", "errors", "skipped", "time")


def command(*args: str) -> str:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=30, cwd=REPO, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    return result.stdout.strip() or "unavailable"


def junit(path: str) -> dict[str, str]:
    if not path or not Path(path).is_file():
        return {}
    root = ET.parse(path).getroot()  # noqa: S314 - JUnit XML this script's own pytest run wrote
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return {}
    return {key: suite.get(key, "0") for key in ("tests", "failures", "errors", "skipped", "time")}


def status_text(code: str) -> str:
    return {"0": "✅ pass", "skipped": "⏭ skipped (fake platform not running)"}.get(
        code, f"❌ fail ({code})"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--suite", action="append", default=[], help="name=junit-path=exit-status")
    args = parser.parse_args()

    dirty = "yes" if command("git", "status", "--porcelain") not in {"", "unavailable"} else "no"
    lines = [
        "# Person 5 acceptance evidence",
        "",
        f"- Generated: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"- Commit: `{command('git', 'rev-parse', 'HEAD')}`",
        f"- Branch: `{command('git', 'branch', '--show-current')}` (uncommitted changes: {dirty})",
        f"- Host: {platform.machine()} / {platform.platform()}",
        f"- Python: {platform.python_version()}; uv: {command('uv', '--version')}",
        f"- Docker: {command('docker', 'version', '--format', DOCKER_FORMAT)}",
        "",
        "## Suites",
        "",
        "| Suite | Result | Tests | Failures | Errors | Skipped | Seconds |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for spec in args.suite:
        name, path, code = spec.split("=")
        counts = junit(path)
        lines.append(
            f"| {name} | {status_text(code)} | "
            + " | ".join(counts.get(k, "-") for k in COUNT_KEYS)
            + " |"
        )
    lines += ["", "## Pinned agent images", ""]
    pinned = sorted((REPO / ".e2e" / "manifests").glob("*.yaml"))
    if not pinned:
        lines.append("_No pinned manifests (run `make e2e-images` or `make images`)._")
    for manifest in pinned:
        image = next(
            (
                ln.split("image:", 1)[1].strip()
                for ln in manifest.read_text().splitlines()
                if "image:" in ln
            ),
            "?",
        )
        lines.append(f"- `{manifest.stem}`: `{image}`")
    lines += [
        "",
        "## Not covered here",
        "",
        "- Live Google/Twilio runs and GB10 hardware evidence: see docs/release/checklist.md",
        "  and docs/demo/operator-script.md.",
    ]
    (args.out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
