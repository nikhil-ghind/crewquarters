"""The watch loop with a fake context and clock: cooldown, camera glitches, and the time limit."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from crewquarters.errors import PlatformError
from intruder_watch import agent as watch


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def ctx(frames: list[Any], seen: list[bool], config: dict[str, Any]) -> Any:
    emails: list[tuple[str, str]] = []

    async def frame() -> dict[str, str]:
        item = frames.pop(0)
        if isinstance(item, Exception):
            raise item
        return {"mediaType": "image/png", "data": item}

    async def chat(profile: str, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        assert profile == "local.vision" and messages[0]["images"][0]["data"]
        return SimpleNamespace(
            parsed=watch.Verdict(person_visible=seen.pop(0), description="A person.")
        )

    async def notify_owner(subject: str, text: str, image: dict[str, str]) -> str:
        emails.append((subject, image["data"]))
        return "sent-1"

    async def log(level: str, message: str) -> None:
        return None

    return SimpleNamespace(
        config=watch.WatchConfig.model_validate({"cameraUrl": "http://cam/snap", **config}),
        camera=SimpleNamespace(frame=frame),
        llm=SimpleNamespace(chat=chat),
        google=SimpleNamespace(gmail=SimpleNamespace(notify_owner=notify_owner)),
        events=SimpleNamespace(log=log),
        emails=emails,
    )


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(watch.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(watch.asyncio, "sleep", fake.sleep)
    return fake


async def test_one_email_per_cooldown_and_camera_glitches_are_skipped(clock: Clock) -> None:
    glitch = PlatformError("camera down", code="PROVIDER_UNAVAILABLE")
    frames: list[Any] = ["f1", glitch, "f3", "f4"]
    # 1 minute at 20 s intervals: checks at 0, 20, 40, 60 s; cooldown 1 minute.
    run = ctx(
        frames,
        [True, True, True],
        {"intervalSeconds": 20, "durationMinutes": 1, "cooldownMinutes": 1},
    )
    result = await watch.run(run)
    assert result.checks == 3 and frames == []
    assert run.emails == [("Intruder alert", "f1"), ("Intruder alert", "f4")]
    assert len(result.alerts) == 2


async def test_check_time_does_not_stretch_the_interval(clock: Clock) -> None:
    run = ctx(
        ["f1", "f2", "f3"], [False, False, False], {"intervalSeconds": 10, "durationMinutes": 0}
    )
    run.config.duration_minutes = 20 / 60  # checks at 0, 10 and 20 s
    started: list[float] = []
    inner = run.llm.chat

    async def slow_chat(*args: Any, **kwargs: Any) -> Any:
        started.append(clock.now)
        clock.now += 3  # each check takes 3 s
        return await inner(*args, **kwargs)

    run.llm.chat = slow_chat
    result = await watch.run(run)
    assert result.checks == 3 and started == [0, 10, 20]


async def test_no_person_no_email(clock: Clock) -> None:
    run = ctx(["f1"], [False], {"durationMinutes": 0})
    result = await watch.run(run)
    assert result.checks == 1 and result.alerts == [] and run.emails == []
