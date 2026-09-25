"""Broker routes (/internal/v1/sdk) from packages/contracts/broker-sdk.openapi.yaml."""

from fastapi import APIRouter

from crewquarters_fake.broker import actions, google, input, knowledge, lifecycle, llm, telephony

router = APIRouter(prefix="/internal/v1/sdk")
for module in (lifecycle, input, actions, llm, knowledge, google, telephony):
    router.include_router(module.router)
