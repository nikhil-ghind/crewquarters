"""The canonical run-event schema (``packages/contracts/events/run-event.schema.json``).

Agent events are validated against it before they are stored, the same check the fake
platform applies, so an event the fake accepts is one the real platform accepts.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_PATH = "events/run-event.schema.json"


@lru_cache(maxsize=4)
def event_validator(contracts_dir: Path) -> Draft202012Validator:
    schema = json.loads((contracts_dir / SCHEMA_PATH).read_text())
    return Draft202012Validator(schema)
