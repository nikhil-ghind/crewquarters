# Intruder Watch

Every `intervalSeconds` (default 10) the agent takes a snapshot from your camera, asks the local
vision model (`local.vision`, Qwen2.5-VL 7B on the GB10) whether a person is in view, and, if so,
emails the snapshot to your own Gmail address. At most one email per `cooldownMinutes` (default 5).
One run watches for `durationMinutes` (default 60, up to 720; 0 checks once).

Everything goes through the SDK and the capability broker:

| Step | SDK call | Capability |
| --- | --- | --- |
| Snapshot | `ctx.camera.frame()` | `camera.snapshot:config` (the `cameraUrl` in config) |
| Check | `ctx.llm.chat("local.vision", [..., images])` | `llm.profile:local.vision.small` |
| Alert | `ctx.google.gmail.notify_owner(...)` | `google.gmail.send` (your own address only) |

## Setup

1. Models: install **Vision (small)**.
2. Connections: connect Google with **Read Gmail** and **Email alerts to you** (read lets the broker
   learn your address).
3. Crew: add Intruder Watch and set `cameraUrl` to a snapshot URL that returns a JPEG or PNG, for
   example `http://192.168.1.20/snapshot.jpg` (credentials can go in the URL).
4. Start and stop: **Run now** / **Stop** on the agent's page, or add a schedule (for example 22:00
   every day with `durationMinutes: 480`). The **Enabled** switch turns scheduled runs off.

In fake provider mode, `http://camera.example.com/snapshot.jpg` is a synthetic camera.

## Tests

`uv run pytest agents/intruder_watch` runs the default scenario on the fake platform and unit tests
of the loop (cooldown, camera glitches, time limit).
