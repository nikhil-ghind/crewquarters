"""Broker routes (/internal/v1/sdk) from packages/contracts/broker-sdk.openapi.yaml."""

from fastapi import APIRouter

from crewquarters_fake.broker import idempotency, input, lifecycle

router = APIRouter(prefix="/internal/v1/sdk")
router.include_router(lifecycle.router)
router.include_router(input.router)
router.include_router(idempotency.router)
