"""Each service process imports only its own package. Its models' foreign keys must still
resolve there, or the first flush fails with NoReferencedTableError (the test suite imports
every model into one process, which hides this)."""

from __future__ import annotations

import subprocess
import sys

import pytest

CHECK = """
import importlib, sys
importlib.import_module(sys.argv[1])
from crewquarters_shared.db.base import Base
missing = []
for table in Base.metadata.tables.values():
    for fk in table.foreign_keys:
        try:
            fk.column
        except Exception:
            missing.append(f"{table.name}.{fk.parent.name} -> {fk.target_fullname}")
print("\\n".join(missing))
"""


@pytest.mark.no_db
@pytest.mark.parametrize(
    "entrypoint",
    [
        "crewquarters_api.main",
        "crewquarters_scheduler.main",
        "crewquarters_gateway.main",
        "crewquarters_broker.main",
        "crewquarters_knowledge.main",
        "crewquarters_secret_store.db",
    ],
)
def test_service_models_resolve_their_foreign_keys_alone(entrypoint: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", CHECK, entrypoint], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "", f"unresolved foreign keys: {result.stdout}"
