"""Broker routes (/internal/v1/sdk) from packages/contracts/broker-sdk.openapi.yaml."""

from fastapi import APIRouter

from crewquarters_fake.broker import (
    actions,
    agents,
    github,
    google,
    input,
    knowledge,
    lifecycle,
    llm,
    openai_compat,
    telephony,
    voice,
)

router = APIRouter(prefix="/internal/v1/sdk")
for module in (
    lifecycle,
    input,
    actions,
    agents,
    llm,
    openai_compat,
    knowledge,
    google,
    github,
    telephony,
    voice,
):
    router.include_router(module.router)
