"""Enforce coverage thresholds (PLAN.md section 20: coverage thresholds on
security/state modules). Reads coverage.json from `pytest --cov --cov-report=json`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

TOTAL_MIN = 85.0
MODULE_MIN = 90.0
# Security- and state-critical modules.
CRITICAL = [
    "crewquarters_shared/runs/service.py",
    "crewquarters_shared/runs/states.py",
    "crewquarters_shared/jobs.py",
    "crewquarters_shared/cron.py",
    "crewquarters_shared/capability.py",
    "crewquarters_shared/redaction.py",
    "crewquarters_shared/schema_guard.py",
    "crewquarters_api/security.py",
    "crewquarters_api/deps.py",
    "crewquarters_api/idempotency.py",
    "crewquarters_scheduler/scheduler.py",
    "crewquarters_scheduler/reconciler.py",
    "crewquarters_gateway/manager.py",
    "crewquarters_gateway/inference.py",
    "crewquarters_runtime/specs.py",
]


def main(path: str = "coverage.json") -> int:
    data = json.loads(Path(path).read_text())
    failures: list[str] = []
    total = data["totals"]["percent_covered"]
    if total < TOTAL_MIN:
        failures.append(f"total {total:.1f}% < {TOTAL_MIN}%")
    files = data["files"]
    for suffix in CRITICAL:
        match = next((f for f in files if f.endswith(suffix)), None)
        if match is None:
            failures.append(f"{suffix}: not measured")
            continue
        pct = files[match]["summary"]["percent_covered"]
        status = "ok" if pct >= MODULE_MIN else "FAIL"
        print(f"{status:4} {pct:5.1f}%  {suffix}")
        if pct < MODULE_MIN:
            failures.append(f"{suffix} {pct:.1f}% < {MODULE_MIN}%")
    print(f"total {total:.1f}%")
    for failure in failures:
        print(f"coverage: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
