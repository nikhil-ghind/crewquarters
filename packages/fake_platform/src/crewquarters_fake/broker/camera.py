"""Broker camera route: frames from ``store.camera_frames``, one per call, in turn."""

from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, Depends, Request

from crewquarters_fake.broker import sheet_scope
from crewquarters_fake.broker.audit import audited
from crewquarters_fake.broker.auth import RunAuth, require, run_auth
from crewquarters_fake.timeutil import iso, utcnow

router = APIRouter()


@router.get("/camera/frame")
async def camera_frame(request: Request, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    require(auth, "camera.snapshot:config", "broker.camera.frame")
    sheet_scope.configured(auth.installation.config, "cameraUrl")

    async def call() -> dict[str, Any]:
        store = auth.store
        data = store.camera_frames[store.camera_calls % len(store.camera_frames)]
        store.camera_calls += 1
        raw = base64.b64decode(data)
        kind = "image/png" if raw.startswith(b"\x89PNG") else "image/jpeg"
        return {"mediaType": kind, "data": data, "bytes": len(raw), "capturedAt": iso(utcnow())}

    return await audited(auth, request, "camera", "broker.camera.frame", call)
