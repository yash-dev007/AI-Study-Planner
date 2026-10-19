"""Shared FastAPI dependencies: authentication, authorization, pagination."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.rate_limit import rate_limit
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.entities import Course, User, UserRole
from app.services.auth import get_course_for_user, get_user

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User:
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("Authentication is required.", code="NOT_AUTHENTICATED")
    payload = decode_access_token(credentials.credentials)
    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("Invalid authentication token.", code="INVALID_TOKEN") from exc
    user = get_user(db, user_id)
    request.state.user_id = user.id
    request.state.role = str(user.role)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN:
        raise ForbiddenError("Administrator access is required.", code="ADMIN_REQUIRED")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def get_course(course_id: uuid.UUID, db: DbSession, user: CurrentUser) -> Course:
    """Resolve a course and enforce course-level authorization."""

    return get_course_for_user(db, course_id=course_id, user=user)


CourseDep = Annotated[Course, Depends(get_course)]


def pagination(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> tuple[int, int]:
    return limit, offset


Pagination = Annotated[tuple[int, int], Depends(pagination)]


def auth_rate_limit(request: Request) -> None:
    rate_limit("auth", settings.rate_limit_auth_per_minute)(request)


def upload_rate_limit(request: Request) -> None:
    rate_limit("upload", settings.rate_limit_upload_per_minute)(request)


def default_rate_limit(request: Request) -> None:
    rate_limit("api", settings.rate_limit_default_per_minute)(request)
