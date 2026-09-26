"""intruder-watch: every few seconds, a camera snapshot goes to a local vision model; when a
person is in view, the snapshot is emailed to the owner (at most once per cooldown).

Everything goes through the SDK: frames from ``ctx.camera``, the check from ``ctx.llm``, the
alert through ``ctx.google.gmail.notify_owner``. Stopping the run (Cancel) ends the loop.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from crewquarters import Agent, RunContext
from crewquarters.errors import PlatformError

PROFILE = "local.vision"
PROMPT = (
    "This is a frame from a home security camera. Is any person visible, even partly? "
    "If yes, describe them and what they are doing in one short sentence."
)


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class WatchConfig(CamelModel):
    camera_url: str = Field(pattern=r"^https?://")
    interval_seconds: int = Field(10, ge=5, le=300)
    duration_minutes: int = Field(60, ge=0, le=720)
    cooldown_minutes: int = Field(5, ge=0, le=120)


class Verdict(BaseModel):
    """Answer schema for the vision model."""

    model_config = ConfigDict(title="IntruderVerdict")
    person_visible: bool
    description: str = Field(max_length=300)


class Alert(CamelModel):
    at: datetime
    description: str


class WatchResult(CamelModel):
    checks: int = 0
    alerts: list[Alert] = Field(default_factory=list)


agent = Agent("intruder-watch", config_model=WatchConfig, result_model=WatchResult)


@agent.run
async def run(ctx: RunContext[WatchConfig]) -> WatchResult:
    config = ctx.config
    result = WatchResult()
    next_check = time.monotonic()
    deadline = next_check + config.duration_minutes * 60
    last_alert: float | None = None
    while True:
        try:
            frame = await ctx.camera.frame()
        except PlatformError as exc:
            if exc.code != "PROVIDER_UNAVAILABLE":
                raise
            await ctx.events.log("warning", "camera unavailable; trying again next time")
        else:
            image = {"mediaType": frame["mediaType"], "data": frame["data"]}
            answer = await ctx.llm.chat(
                PROFILE,
                [{"role": "user", "content": PROMPT, "images": [image]}],
                response_model=Verdict,
                temperature=0,
                max_output_tokens=120,
            )
            result.checks += 1
            verdict = answer.parsed
            cooled = (
                last_alert is None or time.monotonic() - last_alert >= config.cooldown_minutes * 60
            )
            if isinstance(verdict, Verdict) and verdict.person_visible and cooled:
                now = datetime.now(UTC)
                await ctx.google.gmail.notify_owner(
                    "Intruder alert",
                    f"A person was seen at {now:%Y-%m-%d %H:%M:%S} UTC.\n\n{verdict.description}",
                    image,
                )
                last_alert = time.monotonic()
                result.alerts.append(Alert(at=now, description=verdict.description))
                await ctx.events.log(
                    "warning", f"person seen; owner emailed: {verdict.description}"
                )
        # A fixed cadence: the time a check takes does not stretch the interval.
        next_check += config.interval_seconds
        if next_check > deadline:
            return result
        await asyncio.sleep(max(0.0, next_check - time.monotonic()))
