import asyncio

import pytest

from crewquarters.errors import (
    Cancelled,
    InvalidInput,
    ModelUnavailable,
    NeedsConnection,
    PermissionDenied,
    PlatformError,
    ProviderError,
    RateLimited,
    error_from_response,
)


def envelope(code: str, message: str = "boom") -> dict[str, object]:
    return {"error": {"code": code, "message": message, "requestId": "r", "details": {"k": 1}}}


@pytest.mark.parametrize(
    ("status", "code", "cls"),
    [
        (403, "CAPABILITY_DENIED", PermissionDenied),
        (403, "PERMISSION_DENIED", PermissionDenied),
        (409, "NEEDS_CONNECTION", NeedsConnection),
        (503, "MODEL_UNAVAILABLE", ModelUnavailable),
        (409, "RUN_CANCELLED", Cancelled),
        (429, "RATE_LIMITED", RateLimited),
        (422, "INVALID_REQUEST", InvalidInput),
        (422, "INVALID_INPUT_SCHEMA", InvalidInput),
        (422, "INPUT_WAIT_BUDGET_EXCEEDED", InvalidInput),
        (422, "UNSUPPORTED_FEATURE", InvalidInput),
        (502, "PROVIDER_ERROR", ProviderError),
        (503, "PROVIDER_UNAVAILABLE", ProviderError),
        (504, "TIMEOUT", ProviderError),
        (409, "RUN_NOT_ACTIVE", PlatformError),
        (401, "UNAUTHENTICATED", PlatformError),
    ],
)
def test_error_codes_map_to_exception_classes(
    status: int, code: str, cls: type[BaseException]
) -> None:
    error = error_from_response(status, envelope(code), "req-1")
    assert type(error) is cls
    assert error.code == code  # type: ignore[attr-defined]
    assert error.request_id == "req-1"  # type: ignore[attr-defined]


def test_platform_error_carries_message_and_details() -> None:
    error = error_from_response(409, envelope("RUN_NOT_ACTIVE", "run is finished"), "req-2")
    assert isinstance(error, PlatformError)
    assert str(error) == "run is finished"
    assert error.details == {"k": 1}
    assert error.retryable is False


def test_unknown_body_falls_back_to_status() -> None:
    assert type(error_from_response(503, "not json", "r")) is ProviderError
    assert type(error_from_response(403, None, "r")) is PermissionDenied
    assert type(error_from_response(422, {}, "r")) is InvalidInput
    assert type(error_from_response(418, {}, "r")) is PlatformError


def test_retryable_defaults() -> None:
    assert ProviderError("x").retryable is True
    assert RateLimited("x").retryable is True
    assert ModelUnavailable("x").retryable is True
    assert PermissionDenied("x").retryable is False
    assert PlatformError("x", retryable=True).retryable is True


def test_cancelled_is_a_cancelled_error_not_an_exception() -> None:
    error = Cancelled("stop", request_id="r")
    assert isinstance(error, asyncio.CancelledError)
    assert not isinstance(error, Exception)
    assert error.code == "RUN_CANCELLED"
    assert error.request_id == "r"
