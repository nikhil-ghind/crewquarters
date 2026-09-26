from pathlib import Path

from crewctl.testing import run_scenario

AGENT_DIR = Path(__file__).resolve().parents[1]


def test_person_in_view_emails_the_owner_with_the_snapshot(tmp_path: Path) -> None:
    outcome = run_scenario(AGENT_DIR, "default", log_dir=tmp_path)
    assert outcome.state == "SUCCEEDED", outcome.log
    assert outcome.result["checks"] == 1
    [alert] = outcome.result["alerts"]
    assert alert["description"] == "A person in a dark jacket at the front door."
