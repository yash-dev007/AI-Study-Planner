"""Authentication and access control services."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.core.security import create_access_token, hash_password, verify_password
from app.models.entities import Course, User, UserRole, UserSettings


def _find_by_email(db: Session, email: str) -> User | None:
    return db.execute(
        select(User).where(func.lower(User.email) == email.strip().lower())
    ).scalar_one_or_none()


def register_user(
    db: Session,
    *,
    name: str,
    email: str,
    password: str,
    role: UserRole,
    department: str = "",
    college_name: str = "",
    timezone: str = "UTC",
) -> User:
    if _find_by_email(db, email) is not None:
        raise ConflictError(
            "An account with this email already exists.", code="EMAIL_ALREADY_REGISTERED"
        )
    user = User(
        name=name.strip(),
        email=email.strip().lower(),
        password_hash=hash_password(password),
        role=role,
        department=department.strip(),
        college_name=college_name.strip(),
        timezone=timezone,
    )
    db.add(user)
    db.flush()
    db.add(UserSettings(user_id=user.id))
    db.flush()
    return user


def authenticate(db: Session, *, email: str, password: str) -> tuple[User, str, int]:
    user = _find_by_email(db, email)
    if user is None or not verify_password(password, user.password_hash):
        raise UnauthorizedError("Incorrect email or password.", code="INVALID_CREDENTIALS")
    if not user.is_active:
        raise ForbiddenError("This account has been deactivated.", code="ACCOUNT_INACTIVE")
    user.last_login_at = datetime.now(UTC)
    token, expires_in = create_access_token(user.id, str(user.role), user.token_version)
    db.flush()
    return user, token, expires_in


def update_profile(
    db: Session,
    *,
    user: User,
    name: str | None = None,
    department: str | None = None,
    college_name: str | None = None,
    timezone: str | None = None,
) -> User:
    if name is not None:
        user.name = name.strip()
    if department is not None:
        user.department = department.strip()
    if college_name is not None:
        user.college_name = college_name.strip()
    if timezone is not None:
        user.timezone = timezone
    db.flush()
    return user


def change_password(
    db: Session, *, user: User, current_password: str, new_password: str
) -> User:
    if not verify_password(current_password, user.password_hash):
        raise UnauthorizedError("The current password is incorrect.", code="WRONG_PASSWORD")
    user.password_hash = hash_password(new_password)
    # Invalidate every previously issued token for this teacher.
    user.token_version += 1
    db.flush()
    return user


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
