"""LiveKit's end-of-turn model, run inside the agent process.

The plugin normally runs inference in a LiveKit worker job's inference process, which needs a
LiveKit worker, and a worker needs the LiveKit API secret. The agent must never hold that, so
this runs the same ONNX runner in a thread instead. The weights are baked into the image at build
time (``HF_HUB_OFFLINE=1`` at run time); without them the session falls back to pause-based
endpointing.
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from typing import Any

log = logging.getLogger(__name__)


class InProcessInference:
    """An ``InferenceExecutor`` that runs the end-of-turn runner in a worker thread."""

    def __init__(self, runner_class: Any) -> None:
        self._runner_class = runner_class
        self._runner: Any = None
        self._lock = asyncio.Lock()

    async def warm_up(self) -> None:
        async with self._lock:
            if self._runner is None:
                runner = self._runner_class()
                await asyncio.to_thread(runner.initialize)
                self._runner = runner

    async def do_inference(self, method: str, data: bytes) -> bytes | None:
        await self.warm_up()
        result: bytes | None = await asyncio.to_thread(self._runner.run, data)
        return result


async def load_turn_detector() -> Any | None:
    """The multilingual end-of-turn model, warmed up; ``None`` if its weights are missing."""
    try:
        with warnings.catch_warnings():
            # Deprecated in favour of livekit.agents.inference.TurnDetector, which is built
            # around LiveKit Cloud inference; this plugin is the fully local model.
            warnings.simplefilter("ignore", DeprecationWarning)
            from livekit.plugins.turn_detector.base import EOUModelBase
            from livekit.plugins.turn_detector.multilingual import (
                MultilingualModel,
                _EUORunnerMultilingual,
            )

        executor = InProcessInference(_EUORunnerMultilingual)

        class InProcessTurnDetector(MultilingualModel):
            def __init__(self) -> None:
                EOUModelBase.__init__(self, model_type="multilingual", inference_executor=executor)

        detector = InProcessTurnDetector()
        await executor.warm_up()
        return detector
    except Exception as exc:  # weights missing, or the plugin changed shape
        log.warning("turn detector unavailable, using pause-based endpointing: %s", exc)
        return None
