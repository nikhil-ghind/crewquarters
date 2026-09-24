"""Structured JSON logging (PLAN.md section 15.1).

Every record carries the service name and, when present, ``request_id``, ``run_id``
and ``event``. Messages and extra fields pass through redaction, so tokens, secrets
and full phone numbers never reach the log files.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from crewquarters_shared.redaction import redact, redact_text

_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}
_EXTRA_FIELDS = ("request_id", "run_id", "event", "job_id", "job_type")


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "service": self.service,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                entry[key] = value
        for key in _EXTRA_FIELDS:
            if getattr(record, key, None) is not None:
                entry[key] = str(getattr(record, key))
        if record.exc_info:
            entry["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(redact(entry), default=str)


def configure_logging(service: str, level: str | None = None) -> None:
    """Configure the root logger. ``CQ_LOG_FORMAT=text`` gives readable local output."""
    handler = logging.StreamHandler()
    if os.environ.get("CQ_LOG_FORMAT", "json") == "text":
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    else:
        handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level or os.environ.get("CQ_LOG_LEVEL", "INFO"))
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).disabled = True
