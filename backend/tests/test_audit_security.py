"""Security regressions found during the phase 2/3 audit.

Covers privilege escalation at self-registration, the CSRF exemption for
Bearer credentials, malformed token-version claims, rate-limit identity and
memory bounds, production configuration guards, and log redaction.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from app.core.config import DEFAULT_JWT_SECRET, Settings, settings
from app.core.csrf import _normalized_origin
from app.core.errors import UnauthorizedError
from app.core.rate_limit import SlidingWindowLimiter, limiter
from app.core.security import hash_password
from app.models.entities import User, UserRole
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

API = "/api/v1"
PASSWORD = "ProfPassw0rd!"


@pytest.fixture(autouse=True)
def _isolated_rate_limiter() -> Iterator[None]:
    """Keep this module's throttling tests independent of the rest of the suite."""

    limiter.reset()
    yield
    limiter.reset()


def _register(
    client: TestClient,
    *,
    email: str | None = None,
    password: str = PASSWORD,
    role: str = "professor",
) -> dict[str, Any]:
    response = client.post(
        f"{API}/auth/register",
        json={
            "name": "Professor",
            "email": email or f"audit-{uuid.uuid4().hex[:8]}@example.edu",
            "password": password,
            "role": role,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# Privilege escalation
# --------------------------------------------------------------------------- #


def test_registration_cannot_create_an_admin(client: TestClient, db: Session) -> None:
    """A client-supplied ``role: admin`` must not grant cross-tenant access."""

    response = client.post(
        f"{API}/auth/register",
        json={
            "name": "Impostor",
            "email": f"admin-{uuid.uuid4().hex[:8]}@example.edu",
            "password": PASSWORD,
            "role": "admin",
        },
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "ROLE_NOT_ALLOWED"
    admins = db.execute(select(User).where(User.role == UserRole.ADMIN)).scalars().all()
    assert admins == []


# --------------------------------------------------------------------------- #
# CSRF guard
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "authorization",
    ["Bearer", "Basic YWJj", "Bearer   ", "Token abc"],
    ids=["scheme-only", "other-scheme", "blank-credentials", "unknown-scheme"],
)
def test_cookie_writes_without_a_real_bearer_token_still_need_csrf(
    client: TestClient, authorization: str
) -> None:
    """Only a usable Bearer credential bypasses the origin check.

    ``deps.get_current_user`` falls back to the session cookie for these
    headers, so exempting them would let a foreign origin mutate data.
    """

    _register(client)
    response = client.patch(
        f"{API}/auth/profile",
        json={"name": "Hijacked"},
        headers={"Origin": "https://evil.example", "Authorization": authorization},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "CSRF_REJECTED"
    assert client.get(f"{API}/auth/me").json()["name"] == "Professor"


def test_a_real_bearer_credential_still_skips_the_csrf_origin_check(
    client: TestClient,
) -> None:
    registration = _register(client)
    response = client.patch(
        f"{API}/auth/profile",
        json={"name": "API Client"},
        headers={
            "Origin": "https://evil.example",
            **_bearer(registration["access_token"]),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "API Client"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("HTTPS://Example.EDU:443", "https://example.edu"),
        ("http://example.edu:80", "http://example.edu"),
        ("https://example.edu:8443", "https://example.edu:8443"),
        ("https://user:pass@example.edu", "https://example.edu"),
        # A trailing-dot host is a distinct origin; failing closed is safe.
        ("https://example.edu.", "https://example.edu."),
        ("null", None),
        ("", None),
        ("javascript:alert(1)", None),
    ],
)
def test_origin_normalization_handles_edge_cases(value: str, expected: str | None) -> None:
    assert _normalized_origin(value) == expected


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("token_version", [None, "not-a-number"])
def test_malformed_token_version_is_rejected_not_a_server_error(
    client: TestClient, token_version: object
) -> None:
    registration = _register(client)
    forged = jwt.encode(
        {
            "sub": registration["user"]["id"],
            "type": "access",
            "ver": token_version,
            "exp": int(time.time()) + 600,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    response = client.get(f"{API}/auth/me", headers=_bearer(forged))
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "INVALID_TOKEN"


def test_a_token_without_a_version_claim_is_treated_as_version_zero(
    client: TestClient,
) -> None:
    registration = _register(client)
    legacy = jwt.encode(
        {
            "sub": registration["user"]["id"],
            "type": "access",
            "exp": int(time.time()) + 600,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    assert client.get(f"{API}/auth/me", headers=_bearer(legacy)).status_code == 200


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #


def test_forwarded_for_cannot_bypass_login_throttling(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "rate_limit_auth_per_minute", 3)
    registration = _register(client)
    email = registration["user"]["email"]

    statuses = [
        client.post(
            f"{API}/auth/login",
            json={"email": email, "password": "wrong-password"},
            headers={"X-Forwarded-For": f"203.0.113.{index}"},
        ).status_code
        for index in range(6)
    ]
    assert 429 in statuses, statuses
    assert statuses[-1] == 429


def test_forwarded_for_is_honoured_when_explicitly_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.requests import Request

    monkeypatch.setattr(settings, "trust_forwarded_for", True)
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"198.51.100.7, 203.0.113.9")],
        "client": ("10.0.0.1", 1234),
    }
    from app.core.rate_limit import client_identity

    assert client_identity(Request(scope)) == "203.0.113.9"

    monkeypatch.setattr(settings, "trust_forwarded_for", False)
    assert client_identity(Request(scope)) == "10.0.0.1"


def test_rate_limiter_bounds_its_key_map() -> None:
    bounded = SlidingWindowLimiter(max_keys=3)
    for index in range(20):
        bounded.check(f"identity-{index}", limit=5)
    assert len(bounded._events) <= 3


def test_change_password_is_rate_limited(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "rate_limit_auth_per_minute", 2)
    registration = _register(client)
    headers = _bearer(registration["access_token"])

    statuses = [
        client.post(
            f"{API}/auth/change-password",
            json={"current_password": "wrong-password", "new_password": "BrandNewPass1!"},
            headers=headers,
        ).status_code
        for _ in range(3)
    ]
    assert statuses[0] == 401
    assert 429 in statuses, statuses


# --------------------------------------------------------------------------- #
# Session cookie
# --------------------------------------------------------------------------- #


def test_cookie_lifetime_matches_the_token_and_logout_expires_it(client: TestClient) -> None:
    registration = _register(client)
    assert registration["expires_in"] == settings.access_token_expire_minutes * 60

    login = client.post(
        f"{API}/auth/login",
        json={"email": registration["user"]["email"], "password": PASSWORD},
    )
    set_cookie = login.headers.get("set-cookie", "").lower()
    assert f"max-age={settings.access_token_expire_minutes * 60}" in set_cookie

    logout = client.post(f"{API}/auth/logout", headers={"Origin": "http://testserver"})
    assert logout.status_code == 204
    deletion = logout.headers.get("set-cookie", "").lower()
    assert settings.access_token_cookie in deletion
    assert "max-age=0" in deletion
    assert "path=/" in deletion
    assert client.cookies.get(settings.access_token_cookie) is None


# --------------------------------------------------------------------------- #
# User enumeration
# --------------------------------------------------------------------------- #


def test_login_for_an_unknown_email_still_verifies_a_password(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.auth as auth_service

    verified: list[str] = []
    original = auth_service.verify_password

    def spy(password: str, hashed: str) -> bool:
        verified.append(hashed)
        return original(password, hashed)

    monkeypatch.setattr(auth_service, "verify_password", spy)
    with pytest.raises(UnauthorizedError) as excinfo:
        auth_service.authenticate(db, email="nobody@example.edu", password="whatever")
    assert excinfo.value.code == "INVALID_CREDENTIALS"
    assert verified, "a dummy hash must be verified for unknown accounts"


# --------------------------------------------------------------------------- #
# Secret handling
# --------------------------------------------------------------------------- #


def test_integrity_error_logs_do_not_include_statement_parameters(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A duplicate insert must not write the password hash into the logs."""

    import app.api.errors as errors_module

    logged: list[str] = []
    real_warning = errors_module.logger.warning

    def capture(message: str, *args: object, **kwargs: object) -> None:
        logged.append(message % args if args else message)
        real_warning(message, *args, **kwargs)

    monkeypatch.setattr(errors_module.logger, "warning", capture)

    email = f"dupe-{uuid.uuid4().hex[:8]}@example.edu"
    db.add(
        User(
            name="Existing",
            email=email,
            password_hash=hash_password(PASSWORD),
            role=UserRole.PROFESSOR,
        )
    )
    db.commit()
    # Simulate the race where the duplicate check passes but the unique
    # constraint still fires.
    monkeypatch.setattr("app.services.auth._find_by_email", lambda _db, _email: None)

    duplicate = client.post(
        f"{API}/auth/register",
        json={"name": "Duplicate", "email": email, "password": PASSWORD, "role": "professor"},
    )
    assert duplicate.status_code == 409, duplicate.text
    assert logged, "the constraint violation must still be logged"
    assert not any("$argon2" in entry for entry in logged)


# --------------------------------------------------------------------------- #
# Production configuration
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("secret", [DEFAULT_JWT_SECRET, "short-secret"])
def test_production_refuses_a_weak_jwt_secret(secret: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", jwt_secret=secret)  # type: ignore[call-arg]


def test_unsupported_jwt_algorithm_is_refused() -> None:
    with pytest.raises(ValidationError):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            app_env="production",
            jwt_secret="x" * 40,
            jwt_algorithm="none",
        )


def test_production_refuses_a_wildcard_cors_origin() -> None:
    with pytest.raises(ValidationError):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            app_env="production",
            jwt_secret="x" * 40,
            cors_origins=["*"],
        )


def test_development_keeps_the_documented_default_secret() -> None:
    built = Settings(  # type: ignore[call-arg]
        _env_file=None, app_env="development", jwt_secret=DEFAULT_JWT_SECRET
    )
    assert built.jwt_secret == DEFAULT_JWT_SECRET
