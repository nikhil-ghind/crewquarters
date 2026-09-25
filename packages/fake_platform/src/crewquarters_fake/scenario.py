"""Load a scenario directory (spec section 6.4) into the store's providers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from crewquarters_fake.llm_rules import RuleSet
from crewquarters_fake.mailbox import build_message, expand_mailbox
from crewquarters_fake.store import AutoAnswer, Store, utcnow


def _data(root: Path, value: Any) -> Any:
    """A scenario field is either inline data or a file name relative to the scenario directory."""
    if isinstance(value, str):
        return yaml.safe_load((root / value).read_text(encoding="utf-8"))
    return value


def load(store: Store, path: Path, now: datetime | None = None) -> dict[str, Any]:
    root = Path(path)
    doc = yaml.safe_load((root / "scenario.yaml").read_text(encoding="utf-8")) or {}
    tz = ZoneInfo(doc.get("timezone", "UTC"))
    now = now or utcnow()
    store.reset_providers()

    store.connections.update(doc.get("connections", {}))
    mailbox = expand_mailbox(_data(root, doc.get("gmail", {}).get("mailbox", [])) or [], tz, now)
    store.gmail.load([build_message(spec, tz, now) for spec in mailbox])
    store.sheets.load(_data(root, doc.get("sheets", {}).get("spreadsheets", {})) or {})
    store.twilio.load(_data(root, doc.get("twilio", {}).get("outcomes", {})) or {})
    llm = doc.get("llm", {})
    store.gateway.rules = RuleSet.from_list(_data(root, llm.get("rules", [])) or [])
    store.gateway.cold_start_seconds = float(llm.get("coldStartSeconds", 0))
    for kb_id, directory in doc.get("knowledge", {}).items():
        store.knowledge.load_dir(kb_id, root / directory)
    store.auto_answers = [
        AutoAnswer(a["keyPattern"], a.get("data"), float(a.get("delaySeconds", 0)))
        for a in doc.get("inputs", {}).get("autoAnswers", [])
    ]
    return {
        "name": doc.get("name", root.name),
        "timezone": str(tz),
        "messages": len(mailbox),
        "knowledgeBases": sorted(doc.get("knowledge", {})),
    }
