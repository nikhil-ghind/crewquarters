"""Drive one agent attempt end to end: create or re-dispatch a run, launch it, and collect the outcome."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from crewquarters_fake.client import FakePlatformClient
from crewquarters_fake.launcher import LaunchHandle


class Launcher(Protocol):
    def broker_url_for(self, fake_url: str) -> str: ...

    def start(self, dispatch: dict[str, Any]) -> LaunchHandle: ...


@dataclass
class RunOutcome:
    run: dict[str, Any]
    events: list[dict[str, Any]]
    exit_code: int | None
    log: str
    timed_out: bool = False

    @property
    def state(self) -> str:
        return str(self.run["state"])

    @property
    def result(self) -> Any:
        return self.run["result"]

    @property
    def error(self) -> Any:
        return self.run["error"]

    def events_of(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == event_type]


def run_agent(
    client: FakePlatformClient,
    launcher: Launcher,
    installation_id: str | None = None,
    *,
    trigger: str = "manual",
    scheduled_for: str | None = None,
    timeout: float = 60.0,
    run_id: str | None = None,
    on_launch: Callable[[LaunchHandle, str], None] | None = None,
) -> RunOutcome:
    if run_id is None:
        if installation_id is None:
            raise ValueError("pass installation_id for a new run or run_id to re-dispatch a retried run")
        run_id = client.create_run(installation_id, trigger=trigger, scheduled_for=scheduled_for)["id"]
    dispatch = client.dispatch(run_id, launcher.broker_url_for(client.base_url))
    handle = launcher.start(dispatch)
    timed_out = False
    try:
        if on_launch is not None:
            on_launch(handle, run_id)
        exit_code: int | None = handle.wait(timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        handle.kill()
        exit_code = handle.wait(15)
    finally:
        if handle.process.poll() is None:
            handle.kill()
            handle.wait(15)
    client.report_exit(run_id, int(dispatch["attempt"]), exit_code if exit_code is not None else -1)
    return RunOutcome(client.get_run(run_id), client.events(run_id), exit_code, handle.log_text(), timed_out)
