"""The fake platform runs a real SDK agent as a local process end to end."""

from pathlib import Path

from crewquarters_contracts.manifest import load_manifest
from crewquarters_fake.client import FakePlatformClient
from crewquarters_fake.harness import run_agent
from crewquarters_fake.launcher import ProcessLauncher

HELLO = Path(__file__).parent / "agents" / "hello_agent"


def test_hello_agent_runs_and_receives_an_auto_answer(
    fake_client: FakePlatformClient, tmp_path: Path
) -> None:
    manifest = load_manifest(HELLO / "manifest.yaml")
    fake_client.register_manifest(manifest)
    installation = fake_client.install("hello-agent", "0.1.0", {})
    fake_client.add_auto_answer("greeting-*", {"choice": "yes"})
    launcher = ProcessLauncher(manifest["spec"]["entrypoint"], agent_dir=HELLO, log_dir=tmp_path)

    outcome = run_agent(fake_client, launcher, installation["id"], timeout=30)

    assert outcome.state == "SUCCEEDED", outcome.log
    assert outcome.exit_code == 0
    assert outcome.result == {"said": "hello", "trigger": "manual", "attempt": 1}
    statuses = [e["payload"]["to"] for e in outcome.events_of("status")]
    assert statuses == ["PREPARING", "RUNNING", "WAITING_INPUT", "RUNNING", "SUCCEEDED"]
    assert [e["payload"]["message"] for e in outcome.events_of("progress")] == ["starting"]


def test_scheduled_run_reports_its_trigger(fake_client: FakePlatformClient, tmp_path: Path) -> None:
    manifest = load_manifest(HELLO / "manifest.yaml")
    fake_client.register_manifest(manifest)
    installation = fake_client.install("hello-agent", "0.1.0", {"greeting": "hi"})
    fake_client.add_auto_answer("greeting-*", {"choice": "yes"})
    launcher = ProcessLauncher(manifest["spec"]["entrypoint"], agent_dir=HELLO, log_dir=tmp_path)

    outcome = run_agent(
        fake_client, launcher, installation["id"], trigger="schedule", scheduled_for="2026-09-24T04:30:00Z"
    )

    assert outcome.result == {"said": "hi", "trigger": "schedule", "attempt": 1}


def test_killed_agent_is_interrupted_and_retry_succeeds(
    fake_client: FakePlatformClient, tmp_path: Path
) -> None:
    manifest = load_manifest(HELLO / "manifest.yaml")
    fake_client.register_manifest(manifest)
    installation = fake_client.install("hello-agent", "0.1.0", {})
    launcher = ProcessLauncher(manifest["spec"]["entrypoint"], agent_dir=HELLO, log_dir=tmp_path)

    def kill_when_waiting(handle, run_id):  # type: ignore[no-untyped-def]
        fake_client.wait_for(lambda: fake_client.get_run(run_id)["state"] == "WAITING_INPUT", timeout=20)
        handle.kill()

    first = run_agent(fake_client, launcher, installation["id"], on_launch=kill_when_waiting)
    assert first.state == "INTERRUPTED"

    fake_client.retry(first.run["id"])
    fake_client.add_auto_answer("greeting-*", {"choice": "no"})
    pending = fake_client.input_requests(state="pending")
    fake_client.answer(pending[0]["id"], pending[0]["version"], {"choice": "yes"})
    second = run_agent(fake_client, launcher, run_id=first.run["id"])

    assert second.state == "SUCCEEDED", second.log
    assert second.result == {"said": "hello", "trigger": "manual", "attempt": 2}
