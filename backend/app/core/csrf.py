"""Cross-site request forgery protection for cookie-authenticated requests.

Bearer tokens are attached explicitly by API clients and are therefore not
vulnerable to CSRF. The HttpOnly session cookie *is* attached automatically by
the browser, so every unsafe request that authenticates through the cookie must
prove that it came from this application: the ``Origin`` header (or, when a
privacy setting suppresses it, ``Referer``) must match the request host or one
of the configured CORS origins.
"""

from __future__ import annotations

from http.cookies import SimpleCookie
from urllib.parse import urlsplit

from starlette.requests import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.schemas.common import ErrorBody, ErrorResponse

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# These routes establish a session; a cookie they happen to carry does not
# authorize anything yet, and rejecting them would break simple API clients.
PUBLIC_AUTH_PATHS = frozenset(
    {
        f"{settings.api_v1_prefix}/auth/login",
        f"{settings.api_v1_prefix}/auth/register",
    }
)
CSRF_ERROR = ErrorResponse(
    error=ErrorBody(
        code="CSRF_REJECTED",
        message="This request was blocked because it did not come from LecturePlan.",
    )
)


def _normalized_origin(value: str | None) -> str | None:
    """Return ``scheme://host[:port]`` in lowercase, or ``None`` if unusable."""

    if not value:
        return None
    parts = urlsplit(value.strip())
    if not parts.scheme or not parts.hostname:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    try:
        port = parts.port
    except ValueError:
        return None
    if port in (None, {"http": 80, "https": 443}.get(scheme)):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _is_bearer_request(headers: Headers) -> bool:
    """True only when the header carries a real Bearer credential.

    ``deps.get_current_user`` falls back to the session cookie whenever the
    ``Authorization`` header has no usable Bearer credentials (empty, another
    scheme, or a scheme without a token). Exempting those requests from CSRF
    would let a cookie-authenticated write skip the origin check.
    """

    scheme, _, credentials = headers.get("authorization", "").partition(" ")
    return scheme.lower() == "bearer" and bool(credentials.strip())


def _has_session_cookie(cookie_header: str) -> bool:
    if not cookie_header:
        return False
    jar = SimpleCookie()
    try:
        jar.load(cookie_header)
    except Exception:  # pragma: no cover - SimpleCookie rarely raises
        return False
    return settings.access_token_cookie in jar


def _request_origin(scope: Scope, headers: Headers) -> str | None:
    host = headers.get("host")
    if not host:
        return None
    scheme = scope.get("scheme", "http")
    forwarded_proto = headers.get("x-forwarded-proto")
    if forwarded_proto:
        # Browsers cannot set this header on a cross-site simple request, so it
        # is a trustworthy hint from a TLS-terminating reverse proxy.
        scheme = forwarded_proto.split(",")[0].strip() or scheme
    return _normalized_origin(f"{scheme}://{host}")


def _allowed_origins(scope: Scope, headers: Headers) -> set[str]:
    origins = {
        normalized
        for origin in settings.cors_origins
        if (normalized := _normalized_origin(origin)) is not None
    }
    own_origin = _request_origin(scope, headers)
    if own_origin is not None:
        origins.add(own_origin)
    return origins


def _is_same_origin(scope: Scope, headers: Headers) -> bool:
    allowed = _allowed_origins(scope, headers)
    declared = headers.get("origin")
    if declared is not None:
        return _normalized_origin(declared) in allowed
    # Browsers send Origin on unsafe requests. Fall back to Referer for older
    # clients and to Sec-Fetch-Site where Referer is suppressed.
    referer = headers.get("referer")
    if referer:
        return _normalized_origin(referer) in allowed
    return headers.get("sec-fetch-site") == "same-origin"


class CookieCsrfMiddleware:
    """Pure ASGI middleware rejecting foreign cookie-authenticated writes."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", ""))
        if method not in UNSAFE_METHODS or path in PUBLIC_AUTH_PATHS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if _is_bearer_request(headers):
            # Bearer authentication is explicit and not vulnerable to CSRF.
            await self.app(scope, receive, send)
            return
        if not _has_session_cookie(headers.get("cookie", "")):
            await self.app(scope, receive, send)
            return
        if _is_same_origin(scope, headers):
            await self.app(scope, receive, send)
            return

        response = JSONResponse(status_code=403, content=CSRF_ERROR.model_dump(mode="json"))
        await response(scope, receive, send)
