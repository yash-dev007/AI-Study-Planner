"""Authentication and access control services."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.core.security import create_access_token, hash_password, verify_password
from app.models.entities import Course, User, UserRole


def _find_by_email(db: Session, email: str) -> User | None:
    return db.execute(
        select(User).where(func.lower(User.email) == email.strip().lower())
    ).scalar_one_or_none()


def register_user(db: Session, *, name: str, email: str, password: str, role: UserRole) -> User:
    if _find_by_email(db, email) is not None:
        raise ConflictError(
            "An account with this email already exists.", code="EMAIL_ALREADY_REGISTERED"
        )
    user = User(
        name=name.strip(),
        email=email.strip().lower(),
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.flush()
    return user


def authenticate(db: Session, *, email: str, password: str) -> tuple[User, str, int]:
    user = _find_by_email(db, email)
    if user is None or not verify_password(password, user.password_hash):
        raise UnauthorizedError("Incorrect email or password.", code="INVALID_CREDENTIALS")
    if not user.is_active:
        raise ForbiddenError("This account has been deactivated.", code="ACCOUNT_INACTIVE")
    user.last_login_at = datetime.now(UTC)
    token, expires_in = create_access_token(user.id, str(user.role))
    db.flush()
    return user, token, expires_in


def get_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("Your account is not available.", code="ACCOUNT_INACTIVE")
    return user


def get_course_for_user(db: Session, *, course_id: uuid.UUID, user: User) -> Course:
    """Course-level authorization: professors own their courses, admins oversee."""

    course = db.get(Course, course_id)
    if course is None:
        raise NotFoundError("Course not found.", code="COURSE_NOT_FOUND")
    if user.role == UserRole.ADMIN:
        return course
    if course.professor_id != user.id:
        # Do not leak the existence of another professor's course.
        raise ForbiddenError("You do not have access to this course.", code="COURSE_FORBIDDEN")
    return course
