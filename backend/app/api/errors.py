"""Consistent JSON error envelopes for every failure mode."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError
from app.domain.scheduling import SchedulingError
from app.schemas.common import ErrorBody, ErrorResponse

logger = logging.getLogger(__name__)

STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
}


def _envelope(
    *, status_code: int, code: str, message: str, details: dict[str, object] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error=ErrorBody(code=code, message=message, details=details or {})
        ).model_dump(mode="json"),
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _envelope(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
            problems.append({"field": location or "body", "message": error.get("msg", "invalid")})
        return _envelope(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="VALIDATION_ERROR",
            message="The request contains invalid values.",
            details={"problems": problems},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _envelope(
            status_code=exc.status_code,
            code=STATUS_CODES.get(exc.status_code, "HTTP_ERROR"),
            message=str(exc.detail),
        )

    @app.exception_handler(SchedulingError)
    async def _scheduling_error(_: Request, exc: SchedulingError) -> JSONResponse:
        return _envelope(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="SCHEDULING_ERROR",
            message=str(exc),
        )

    @app.exception_handler(IntegrityError)
    async def _integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
        logger.warning("Database constraint violation: %s", exc)
        return _envelope(
            status_code=status.HTTP_409_CONFLICT,
            code="CONSTRAINT_VIOLATION",
            message=(
                "That change conflicts with existing data, for example a duplicate date or "
                "overlapping lecture slot."
            ),
        )

    @app.exception_handler(SQLAlchemyError)
    async def _database_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("Database error", exc_info=exc)
        return _envelope(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="DATABASE_ERROR",
            message="The database could not complete the request.",
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return _envelope(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="INTERNAL_ERROR",
            message="Something went wrong while processing the request.",
        )
