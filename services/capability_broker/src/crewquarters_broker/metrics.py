"""Broker metrics (PLAN.md section 15.1), served at ``/internal/v1/metrics``.

Labels never carry IDs, tokens, or phone numbers: routes are templates, providers are
``google`` or ``twilio``, and outcomes are status classes or error codes.
"""

from __future__ import annotations

import time

import httpx
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

# Error codes worth watching: authorization failures and cancellations.
DENIAL_CODES = frozenset(
    {"UNAUTHENTICATED", "CAPABILITY_DENIED", "PERMISSION_DENIED", "RUN_NOT_ACTIVE", "RUN_CANCELLED"}
)
_PROVIDERS = {
    "oauth2.googleapis.com": "google",
    "gmail.googleapis.com": "google",
    "sheets.googleapis.com": "google",
    "api.twilio.com": "twilio",
}


class BrokerMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "cq_http_requests_total",
            "HTTP requests by route template, method, and status.",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "cq_http_request_duration_seconds",
            "HTTP request latency (excluding streamed bodies).",
            ["method", "route"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2.5, 5),
            registry=self.registry,
        )
        self.denials = Counter(
            "cq_broker_denials_total",
            "Agent calls refused, by error code.",
            ["code"],
            registry=self.registry,
        )
        self.provider_requests = Counter(
            "cq_broker_provider_requests_total",
            "Calls to Google and Twilio by provider and outcome (2xx, 4xx, 5xx, error).",
            ["provider", "outcome"],
            registry=self.registry,
        )
        self.provider_latency = Histogram(
            "cq_broker_provider_request_duration_seconds",
            "Google and Twilio request latency.",
            ["provider"],
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20),
            registry=self.registry,
        )
        self.oauth_refresh_failures = Counter(
            "cq_broker_oauth_refresh_failures_total",
            "OAuth refreshes that failed; invalid_grant means the owner must reconnect.",
            ["provider", "reason"],
            registry=self.registry,
        )
        self.callback_rejections = Counter(
            "cq_broker_callback_rejections_total",
            "Provider callbacks rejected for a bad signature.",
            ["provider"],
            registry=self.registry,
        )

    def observe_error(self, code: str) -> None:
        if code in DENIAL_CODES:
            self.denials.labels(code).inc()

    def render(self) -> bytes:
        return generate_latest(self.registry)


class MeteredTransport(httpx.AsyncBaseTransport):
    """Counts and times every Google and Twilio request, including transport failures."""

    def __init__(self, inner: httpx.AsyncBaseTransport, metrics: BrokerMetrics) -> None:
        self._inner = inner
        self._metrics = metrics

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        provider = _PROVIDERS.get(request.url.host, "other")
        started = time.perf_counter()
        try:
            response = await self._inner.handle_async_request(request)
        except httpx.HTTPError:
            self._metrics.provider_requests.labels(provider, "error").inc()
            raise
        finally:
            self._metrics.provider_latency.labels(provider).observe(time.perf_counter() - started)
        self._metrics.provider_requests.labels(provider, f"{response.status_code // 100}xx").inc()
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()
