"""Application error types and consistent JSON error envelopes."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for expected, user-facing application errors."""

    status_code: int = 400
    code: str = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class ValidationFailedError(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"


class InfeasibleScheduleError(AppError):
    status_code = 409
    code = "SCHEDULE_INFEASIBLE"


class DocumentProcessingError(AppError):
    status_code = 422
    code = "DOCUMENT_PROCESSING_FAILED"


class RateLimitedError(AppError):
    status_code = 429
    code = "RATE_LIMITED"
