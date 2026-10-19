"""Structured JSON logging and request correlation IDs."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from datetime import UTC, datetime
from typing import Any

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key in (
            "method",
            "path",
            "status_code",
            "duration_ms",
            "user_id",
            "course_id",
            "event",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(debug: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False


class RequestContextMiddleware:
    """Pure ASGI middleware that assigns a request ID and emits access logs."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logging.getLogger("app.access").info(
                "request.completed",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )
            request_id_var.reset(token)
