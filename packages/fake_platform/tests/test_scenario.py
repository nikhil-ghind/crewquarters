from datetime import UTC, datetime
from pathlib import Path

import yaml

from crewquarters_fake.scenario import load
from crewquarters_fake.settings import FakeSettings
from crewquarters_fake.store import Store


def write_scenario(root: Path) -> None:
    (root / "knowledge").mkdir()
    (root / "knowledge" / "doc.md").write_text("Refunds within 30 days.\n")
    (root / "scenario.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "timezone": "Asia/Kolkata",
                "connections": {"google": "expired"},
                "gmail": {"mailbox": "mailbox.yaml"},
                "sheets": {"spreadsheets": {"s1": {"Contacts": [["name"], ["Asha"]]}}},
                "twilio": {"outcomes": {"+15555550101": {"status": "busy"}}},
                "llm": {
                    "rules": [{"name": "r", "match": {}, "respond": {"text": "hi"}}],
                    "coldStartSeconds": 0.5,
                },
                "knowledge": {"kb-demo": "knowledge"},
                "inputs": {
                    "autoAnswers": [{"keyPattern": "confirm-*", "value": {"choice": "approve"}}]
                },
            }
        )
    )
    (root / "mailbox.yaml").write_text(
        yaml.safe_dump(
            [
                {
                    "id": "m1",
                    "subject": "hi",
                    "relative": {"days": -1, "time": "08:00"},
                    "body": {"text": "x"},
                }
            ]
        )
    )


def test_load_scenario_populates_every_provider(tmp_path: Path) -> None:
    write_scenario(tmp_path)
    store = Store(FakeSettings())
    summary = load(store, tmp_path, now=datetime(2026, 9, 24, 12, 0, tzinfo=UTC))
    assert summary["name"] == "demo"
    assert store.connections == {"google": "expired", "twilio": "connected"}
    message = store.gmail.get("m1")
    assert message["internalDate"] == str(
        int(datetime.fromisoformat("2026-09-23T08:00:00+05:30").timestamp()) * 1000
    )
    assert store.sheets.get("s1", "Contacts!A2:A")["values"] == [["Asha"]]
    assert store.twilio.outcomes == {"+15555550101": {"status": "busy"}}
    assert store.gateway.cold_start_seconds == 0.5
    assert store.knowledge.has("kb-demo")
    assert store.auto_answers[0].key_pattern == "confirm-*"


def test_loading_again_replaces_provider_state(tmp_path: Path) -> None:
    write_scenario(tmp_path)
    store = Store(FakeSettings())
    load(store, tmp_path)
    store.sheets.update("s1", "Contacts!A5", [["x"]])
    load(store, tmp_path)
    assert store.sheets.get("s1", "Contacts!A5")["values"] == []
    assert len(store.auto_answers) == 1


def test_generate_entries_expand_into_many_messages(tmp_path: Path) -> None:
    (tmp_path / "scenario.yaml").write_text(
        yaml.safe_dump(
            {
                "timezone": "Asia/Kolkata",
                "gmail": {
                    "mailbox": [
                        {
                            "generate": {
                                "count": 3,
                                "idPrefix": "bulk-",
                                "date": "2026-09-23T08:00:00+05:30",
                                "stepMinutes": 10,
                                "subject": "Bulk {n}",
                                "body": "Newsletter {n}",
                                "category": "updates",
                            }
                        },
                        {
                            "id": "single",
                            "date": "2026-09-23T12:00:00+05:30",
                            "body": {"text": "x"},
                        },
                    ]
                },
            }
        )
    )
    store = Store(FakeSettings())
    summary = load(store, tmp_path)
    assert summary["messages"] == 4
    assert sorted(store.gmail.messages) == ["bulk-001", "bulk-002", "bulk-003", "single"]
    third = store.gmail.get("bulk-003")
    headers = {h["name"]: h["value"] for h in third["payload"]["headers"]}
    assert headers["Subject"] == "Bulk 3"
    assert "CATEGORY_UPDATES" in third["labelIds"]
    expected = int(datetime.fromisoformat("2026-09-23T08:20:00+05:30").timestamp()) * 1000
    assert third["internalDate"] == str(expected)
