"""Security tests: password hashing, tokens, rate limiting, and upload safety."""

from __future__ import annotations

import time
import uuid

import jwt
import pytest
from app.core.config import settings
from app.core.errors import UnauthorizedError
from app.core.rate_limit import SlidingWindowLimiter
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.services.syllabus import safe_filename, storage_path
from fastapi.testclient import TestClient

API = "/api/v1"


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #


def test_passwords_are_hashed_with_a_slow_kdf() -> None:
    hashed = hash_password("Sup3rSecret!")
    assert hashed != "Sup3rSecret!"
    assert hashed.startswith("$argon2"), "argon2 is the configured password hasher"
    assert verify_password("Sup3rSecret!", hashed)
    assert not verify_password("sup3rsecret!", hashed)


def test_same_password_hashes_differently() -> None:
    assert hash_password("same-password") != hash_password("same-password"), "each hash is salted"


def test_verify_password_handles_malformed_hashes() -> None:
    assert verify_password("anything", "not-a-hash") is False


def test_registered_password_is_never_stored_in_clear_text(client: TestClient, db) -> None:  # type: ignore[no-untyped-def]
    email = f"hash-{uuid.uuid4().hex[:6]}@example.edu"
    response = client.post(
        f"{API}/auth/register",
        json={"name": "Hash", "email": email, "password": "Plaintext1!", "role": "professor"},
    )
    assert response.status_code == 201
    assert "password" not in response.json()

    from app.models.entities import User
    from sqlalchemy import select

    user = db.execute(select(User).where(User.email == email)).scalar_one()
    assert user.password_hash != "Plaintext1!"
    assert verify_password("Plaintext1!", user.password_hash)


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #


def test_access_token_round_trip() -> None:
    user_id = uuid.uuid4()
    token, expires_in = create_access_token(user_id, "professor")
    payload = decode_access_token(token)
    assert payload["sub"] == str(user_id)
    assert payload["role"] == "professor"
    assert expires_in == settings.access_token_expire_minutes * 60


def test_token_signed_with_another_secret_is_rejected() -> None:
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "type": "access", "exp": int(time.time()) + 600},
        "some-other-secret",
        algorithm="HS256",
    )
    with pytest.raises(UnauthorizedError) as excinfo:
        decode_access_token(forged)
    assert excinfo.value.code == "INVALID_TOKEN"


def test_expired_token_is_rejected() -> None:
    expired = jwt.encode(
        {"sub": str(uuid.uuid4()), "type": "access", "exp": int(time.time()) - 60},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(UnauthorizedError) as excinfo:
        decode_access_token(expired)
    assert excinfo.value.code == "TOKEN_EXPIRED"


def test_refresh_style_token_is_not_accepted_as_an_access_token() -> None:
    other = jwt.encode(
        {"sub": str(uuid.uuid4()), "type": "refresh", "exp": int(time.time()) + 600},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(UnauthorizedError):
        decode_access_token(other)


def test_missing_and_malformed_authorization_headers_are_rejected(client: TestClient) -> None:
    assert client.get(f"{API}/auth/me").status_code == 401
    assert (
        client.get(f"{API}/auth/me", headers={"Authorization": "Bearer nonsense"}).status_code
        == 401
    )
    assert client.get(f"{API}/auth/me", headers={"Authorization": "Basic abc"}).status_code == 401


def test_token_for_a_deleted_user_is_rejected(client: TestClient, db) -> None:  # type: ignore[no-untyped-def]
    email = f"gone-{uuid.uuid4().hex[:6]}@example.edu"
    registered = client.post(
        f"{API}/auth/register",
        json={"name": "Gone", "email": email, "password": "Gone12345!", "role": "professor"},
    )
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}

    from app.models.entities import User
    from sqlalchemy import select

    user = db.execute(select(User).where(User.email == email)).scalar_one()
    db.delete(user)
    db.commit()

    response = client.get(f"{API}/auth/me", headers=headers)
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #


def test_rate_limiter_blocks_after_the_limit() -> None:
    limiter = SlidingWindowLimiter()
    for _ in range(3):
        limiter.check("key", limit=3, window_seconds=60)
    from app.core.errors import RateLimitedError

    with pytest.raises(RateLimitedError):
        limiter.check("key", limit=3, window_seconds=60)


def test_rate_limiter_window_expires() -> None:
    limiter = SlidingWindowLimiter()
    limiter.check("expiring", limit=1, window_seconds=1)
    time.sleep(1.05)
    limiter.check("expiring", limit=1, window_seconds=1)


def test_login_attempts_are_rate_limited(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "rate_limit_auth_per_minute", 3)
    email = f"limit-{uuid.uuid4().hex[:6]}@example.edu"
    client.post(
        f"{API}/auth/register",
        json={"name": "Limit", "email": email, "password": "Limit12345!", "role": "professor"},
    )
    statuses = [
        client.post(f"{API}/auth/login", json={"email": email, "password": "wrong"}).status_code
        for _ in range(6)
    ]
    assert 429 in statuses, statuses
    throttled = [status for status in statuses if status == 429]
    assert throttled
    assert statuses[-1] == 429


# --------------------------------------------------------------------------- #
# Upload and storage safety
# --------------------------------------------------------------------------- #


def test_filenames_cannot_escape_the_storage_directory() -> None:
    assert safe_filename("../../etc/passwd") == "passwd"
    escaped = safe_filename("..\\..\\windows\\system32\\cmd.exe")
    assert "\\" not in escaped and "/" not in escaped
    assert escaped.endswith("cmd.exe")
    assert safe_filename("/absolute/path/syllabus.pdf") == "syllabus.pdf"
    assert safe_filename("...") == "syllabus"
    assert "/" not in safe_filename("a/b/c.txt")


def test_storage_path_stays_inside_the_storage_root() -> None:
    course_id = uuid.uuid4()
    document_id = uuid.uuid4()
    path = storage_path(course_id, document_id, "../../evil.txt")
    assert path.parent.is_relative_to(settings.storage_dir)
    assert ".." not in path.parts


def test_oversized_upload_is_rejected(
    client: TestClient, course_id: str, professor_headers: dict[str, str]
) -> None:
    import app.api.routes.syllabus as syllabus_route

    original = syllabus_route.MAX_UPLOAD_BYTES
    syllabus_route.MAX_UPLOAD_BYTES = 64
    try:
        response = client.post(
            f"{API}/courses/{course_id}/syllabus/upload",
            files={"file": ("big.txt", b"x" * 200, "text/plain")},
            headers=professor_headers,
        )
    finally:
        syllabus_route.MAX_UPLOAD_BYTES = original

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_stored_files_are_written_under_the_course_directory(
    client: TestClient, course_id: str, professor_headers: dict[str, str]
) -> None:
    from tests.conftest import upload_syllabus

    preview = upload_syllabus(client, course_id, professor_headers)
    stored = list((settings.storage_dir / "syllabi" / course_id).rglob("syllabus.txt"))
    assert stored, "the upload is kept on disk for audit"
    assert str(stored[0]).endswith(preview["document"]["filename"])


def test_secrets_are_not_returned_by_the_health_endpoint(client: TestClient) -> None:
    body = client.get("/health").text
    assert settings.jwt_secret not in body
    assert "openrouter" not in body or "provider" in body
