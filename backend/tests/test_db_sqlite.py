"""SQLite safety invariants: foreign keys, WAL, timeout, cascade, rollback."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from app.models.entities import Course, CourseStatus, User
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _user(email: str | None = None) -> User:
    return User(
        name="Test Teacher",
        email=email or f"{uuid.uuid4().hex}@example.edu",
        password_hash="not-a-real-hash",
    )


def _course(professor_id: uuid.UUID) -> Course:
    start = date.today()
    return Course(
        name="Test Subject",
        professor_id=professor_id,
        start_date=start,
        end_date=start + timedelta(weeks=8),
        status=CourseStatus.DRAFT,
        lecture_duration_minutes=60,
        total_weeks=8,
        settings={},
        version=1,
    )


def test_sqlite_pragmas_are_enabled(db: Session) -> None:
    assert db.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    assert str(db.execute(text("PRAGMA journal_mode")).scalar_one()).lower() == "wal"
    assert db.execute(text("PRAGMA busy_timeout")).scalar_one() == 5000


def test_foreign_key_violation_is_rejected(db: Session) -> None:
    db.add(_course(professor_id=uuid.uuid4()))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_deleting_a_user_cascades_to_courses(db: Session) -> None:
    user = _user()
    db.add(user)
    db.flush()
    course = _course(professor_id=user.id)
    db.add(course)
    db.commit()
    course_id = course.id
    user_id = user.id

    db.execute(delete(User).where(User.id == user_id))
    db.commit()
    db.expire_all()

    assert db.get(User, user_id) is None
    assert db.get(Course, course_id) is None


def test_failed_multi_record_write_rolls_back_entirely(db: Session) -> None:
    user = _user()
    db.add(user)
    db.flush()
    user_id = user.id
    db.add(_course(professor_id=uuid.uuid4()))  # invalid professor
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    assert db.execute(select(User).where(User.id == user_id)).scalar_one_or_none() is None
