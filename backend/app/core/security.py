"""Password hashing and JWT helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash

from app.core.config import settings
from app.core.errors import UnauthorizedError

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return password_hash.verify(password, hashed)
    except Exception:  # pragma: no cover - defensive for malformed hashes
        return False


def create_access_token(
    user_id: uuid.UUID, role: str, token_version: int = 0
) -> tuple[str, int]:
    expires_in = settings.access_token_expire_minutes * 60
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        # Bumped on password change; tokens with an older version are rejected.
        "ver": token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError(
            "Your session has expired. Please sign in again.", code="TOKEN_EXPIRED"
        ) from exc
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Invalid authentication token.", code="INVALID_TOKEN") from exc
    if payload.get("type") != "access":
        raise UnauthorizedError("Invalid authentication token.", code="INVALID_TOKEN")
    return payload
