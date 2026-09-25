"""Typed SDK exceptions and the broker error-code mapping (spec section 5.4)."""

from __future__ import annotations

import asyncio
from typing import Any


class PlatformError(Exception):
    """Base class for errors reported by the platform or raised by the SDK."""

    code = "PLATFORM_ERROR"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        request_id: str | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.request_id = request_id
        self.details: dict[str, Any] = details or {}


class PermissionDenied(PlatformError):
    code = "CAPABILITY_DENIED"


class NeedsConnection(PlatformError):
    code = "NEEDS_CONNECTION"


class ModelUnavailable(PlatformError):
    code = "MODEL_UNAVAILABLE"
    retryable = True


class RateLimited(PlatformError):
    code = "RATE_LIMITED"
    retryable = True


class InvalidInput(PlatformError):
    code = "INVALID_REQUEST"


class ProviderError(PlatformError):
    code = "PROVIDER_ERROR"
    retryable = True


class OutcomeUnknown(PlatformError):
    """A non-idempotent request may or may not have been applied. Never retried automatically."""

    code = "OUTCOME_UNKNOWN"


class InputTimeout(PlatformError):
    code = "INPUT_TIMEOUT"


class AgentError(PlatformError):
    """Raise from agent code to fail the run with a specific error code."""

    code = "AGENT_ERROR"


class Cancelled(asyncio.CancelledError):
    """The run was cancelled. A CancelledError, so ``except Exception`` cannot swallow it."""

    code = "RUN_CANCELLED"
    retryable = False

    def __init__(self, message: str = "run cancelled", *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.request_id = request_id
        self.details: dict[str, Any] = {}


_CODE_CLASSES: dict[str, type[PlatformError]] = {
    "CAPABILITY_DENIED": PermissionDenied,
    "PERMISSION_DENIED": PermissionDenied,
    "NEEDS_CONNECTION": NeedsConnection,
    "MODEL_UNAVAILABLE": ModelUnavailable,
    "RATE_LIMITED": RateLimited,
    "INVALID_REQUEST": InvalidInput,
    "INVALID_INPUT_SCHEMA": InvalidInput,
    "INPUT_WAIT_BUDGET_EXCEEDED": InvalidInput,
    "UNSUPPORTED_FEATURE": InvalidInput,
    "PROVIDER_ERROR": ProviderError,
    "PROVIDER_UNAVAILABLE": ProviderError,
    "TIMEOUT": ProviderError,
}

_STATUS_CLASSES: dict[int, type[PlatformError]] = {
    403: PermissionDenied,
    422: InvalidInput,
    429: RateLimited,
    502: ProviderError,
    503: ProviderError,
    504: ProviderError,
}


def error_from_response(status: int, body: object, request_id: str) -> PlatformError | Cancelled:
    """Build the typed exception for an error response from the broker."""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        error = {}
    raw_code, raw_message = error.get("code"), error.get("message")
    raw_details = error.get("details")
    code = raw_code if isinstance(raw_code, str) else None
    message = raw_message if isinstance(raw_message, str) else f"broker returned HTTP {status}"
    details = raw_details if isinstance(raw_details, dict) else None
    if code == "RUN_CANCELLED":
        return Cancelled(message, request_id=request_id)
    cls = _CODE_CLASSES.get(code) if code else None
    if cls is None:
        cls = _STATUS_CLASSES.get(status, PlatformError)
    if code is None:
        code = cls.code if cls is not PlatformError else f"HTTP_{status}"
    return cls(message, code=code, request_id=request_id, details=details)
