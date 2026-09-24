"""Platform error type rendered as ``{"error": {code, message, requestId, details}}``."""

from __future__ import annotations

from typing import Any


class PlatformError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def not_found(what: str, ident: object) -> PlatformError:
    return PlatformError("NOT_FOUND", f"{what} not found.", 404, {"id": str(ident)})


def conflict(code: str, message: str, **details: Any) -> PlatformError:
    return PlatformError(code, message, 409, details)


def invalid(code: str, message: str, **details: Any) -> PlatformError:
    return PlatformError(code, message, 422, details)
