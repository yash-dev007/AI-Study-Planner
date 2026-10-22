"""Course configuration services."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.models.entities import (
    ApprovalStatus,
    AvailabilityRule,
    CalendarException,
    Course,
    CourseStatus,
    ScheduleRevision,
    SyllabusUnit,
    Topic,
    User,
    UserRole,
)
from app.schemas.course import (
    AvailabilityRuleInput,
    AvailabilityRuleRead,
    CalendarExceptionInput,
    CalendarExceptionRead,
    CourseCreate,
    CourseDetail,
    CourseRead,
    CourseSettings,
    CourseUpdate,
)

MIN_RULE_MINUTES = 15


def course_settings(course: Course) -> CourseSettings:
    """Parse the JSON settings column into a validated model, ignoring junk."""

    raw = course.settings if isinstance(course.settings, dict) else {}
    try:
        return CourseSettings.model_validate(raw)
    except Exception:
        return CourseSettings()


def lecture_slots_per_week(rules: list[AvailabilityRule], lecture_duration_minutes: int) -> int:
    total = 0
    for rule in rules:
        if not rule.is_enabled:
            continue
        start = rule.start_time.hour * 60 + rule.start_time.minute
        end = rule.end_time.hour * 60 + rule.end_time.minute
        total += max(0, (end - start) // max(MIN_RULE_MINUTES, lecture_duration_minutes))
    return total


def _validate_rule_windows(rules: list[AvailabilityRuleInput]) -> None:
    for rule in rules:
        start = rule.start_time.hour * 60 + rule.start_time.minute
        end = rule.end_time.hour * 60 + rule.end_time.minute
        if end - start < MIN_RULE_MINUTES:
            raise ValidationFailedError(
                "A teaching window must be at least 15 minutes long.",
                code="AVAILABILITY_WINDOW_TOO_SHORT",
                details={"weekday": rule.weekday},
            )


def _weekday_conflicts(rules: list[AvailabilityRuleInput]) -> None:
    """Reject windows that overlap on the same weekday."""

    by_day: dict[int, list[tuple[int, int]]] = {}
    for rule in rules:
        if not rule.is_enabled:
            continue
        start = rule.start_time.hour * 60 + rule.start_time.minute
        end = rule.end_time.hour * 60 + rule.end_time.minute
        for other_start, other_end in by_day.get(rule.weekday, []):
            if start < other_end and end > other_start:
                raise ValidationFailedError(
                    "Two teaching windows overlap on the same weekday.",
                    code="AVAILABILITY_WINDOW_OVERLAP",
                    details={"weekday": rule.weekday},
                )
        by_day.setdefault(rule.weekday, []).append((start, end))


def create_course(
    db: Session, *, professor: User, payload: CourseCreate, actor_id: uuid.UUID
) -> Course:
    _validate_rule_windows(payload.availability_rules)
    _weekday_conflicts(payload.availability_rules)

    course = Course(
        name=payload.name.strip(),
        course_code=payload.course_code.strip() if payload.course_code else None,
        professor_id=professor.id,
        department=payload.department.strip(),
        semester=payload.semester.strip(),
        academic_year=payload.academic_year.strip(),
        start_date=payload.start_date,
        end_date=payload.end_date,
        target_completion_date=payload.target_completion_date,
        total_available_minutes=payload.total_available_minutes,
        timezone=payload.timezone,
        status=CourseStatus.DRAFT,
        lecture_duration_minutes=payload.lecture_duration_minutes,
        total_weeks=payload.total_weeks,
        settings=payload.settings.model_dump(mode="json"),
        version=1,
    )
    db.add(course)
    db.flush()

    for rule in payload.availability_rules:
        db.add(AvailabilityRule(course_id=course.id, **_rule_values(rule)))
    for exception in payload.exceptions:
        db.add(CalendarException(course_id=course.id, **exception.model_dump()))
    db.flush()
    return course


def _rule_values(rule: AvailabilityRuleInput) -> dict[str, object]:
    return {
        "weekday": rule.weekday,
        "start_time": rule.start_time,
        "end_time": rule.end_time,
        "room": rule.room,
        "effective_from": rule.effective_from,
        "effective_until": rule.effective_until,
        "is_enabled": rule.is_enabled,
    }


def list_courses(
    db: Session,
    *,
    user: User,
    status: CourseStatus | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Course], int]:
    conditions = []
    if user.role == UserRole.ADMIN:
        pass
    else:
        conditions.append(Course.professor_id == user.id)
    if status is not None:
        conditions.append(Course.status == status)
    if search:
        needle = f"%{search.strip().lower()}%"
        conditions.append(
            or_(
                func.lower(Course.name).like(needle),
                func.lower(Course.course_code).like(needle),
                func.lower(Course.department).like(needle),
            )
        )

    total = int(
        db.execute(select(func.count()).select_from(Course).where(*conditions)).scalar_one()
    )
    rows = (
        db.execute(
            select(Course)
            .where(*conditions)
            .order_by(Course.start_date.desc(), Course.name)
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def sync_calendar_exceptions(
    db: Session, *, course: Course, exceptions: list[CalendarExceptionInput]
) -> list[CalendarException]:
    """Full replacement of the course's calendar exceptions."""

    wanted = {(item.date, item.exception_type): item for item in exceptions}
    existing = {
        (item.date, item.exception_type): item
        for item in db.execute(
            select(CalendarException).where(CalendarException.course_id == course.id)
        )
        .scalars()
        .all()
    }

    for key, item in wanted.items():
        current = existing.get(key)
        if current is None:
            db.add(CalendarException(course_id=course.id, **item.model_dump()))
        else:
            current.description = item.description
            current.blocks_teaching = item.blocks_teaching

    for key, current in existing.items():
        if key not in wanted:
            db.delete(current)
    db.flush()
    return list(
        db.execute(
            select(CalendarException)
            .where(CalendarException.course_id == course.id)
            .order_by(CalendarException.date)
        )
        .scalars()
        .all()
    )


def replace_availability_rules(
    db: Session, *, course: Course, rules: list[AvailabilityRuleInput]
) -> list[AvailabilityRule]:
    _validate_rule_windows(rules)
    _weekday_conflicts(rules)
    for existing in (
        db.execute(select(AvailabilityRule).where(AvailabilityRule.course_id == course.id))
        .scalars()
        .all()
    ):
        db.delete(existing)
    db.flush()
    created = []
    for rule in rules:
        row = AvailabilityRule(course_id=course.id, **_rule_values(rule))
        db.add(row)
        created.append(row)
    db.flush()
    return created


def add_exception(
    db: Session, *, course: Course, payload: CalendarExceptionInput
) -> CalendarException:
    existing = db.execute(
        select(CalendarException).where(
            CalendarException.course_id == course.id,
            CalendarException.date == payload.date,
            CalendarException.exception_type == payload.exception_type,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            "This date is already marked with the same exception type.",
            code="CALENDAR_EXCEPTION_EXISTS",
        )
    row = CalendarException(course_id=course.id, **payload.model_dump())
    db.add(row)
    db.flush()
    return row


def delete_exception(db: Session, *, course: Course, exception_id: uuid.UUID) -> None:
    row = db.get(CalendarException, exception_id)
    if row is None or row.course_id != course.id:
        raise NotFoundError("Calendar exception not found.", code="CALENDAR_EXCEPTION_NOT_FOUND")
    db.delete(row)
    db.flush()


def update_course(
    db: Session, *, course: Course, payload: CourseUpdate, actor_id: uuid.UUID
) -> Course:
    if payload.version is not None and payload.version != course.version:
        raise ConflictError(
            "This course was changed by someone else while you were editing it. "
            "Reload the course and try again.",
            code="COURSE_VERSION_CONFLICT",
            details={"expected_version": payload.version, "current_version": course.version},
        )

    data = payload.model_dump(exclude_unset=True, exclude={"version"})
    settings_payload = data.pop("settings", None)
    schedule_relevant = False

    for field, value in data.items():
        if field in {
            "start_date",
            "end_date",
            "target_completion_date",
            "total_available_minutes",
            "timezone",
            "lecture_duration_minutes",
            "total_weeks",
        }:
            schedule_relevant = True
        setattr(course, field, value)

    if settings_payload is not None:
        merged = course_settings(course).model_dump()
        merged.update(settings_payload)
        course.settings = CourseSettings.model_validate(merged).model_dump(mode="json")

    if course.end_date < course.start_date:
        raise ValidationFailedError(
            "The semester end date must not precede the start date.", code="INVALID_DATE_RANGE"
        )
    if course.target_completion_date is not None and not (
        course.start_date <= course.target_completion_date <= course.end_date
    ):
        raise ValidationFailedError(
            "The target completion date must fall within the semester.",
            code="INVALID_TARGET_DATE",
        )

    course.version += 1
    db.flush()
    _ = actor_id, schedule_relevant
    return course


def course_detail(db: Session, course: Course) -> CourseDetail:
    rules = list(
        db.execute(select(AvailabilityRule).where(AvailabilityRule.course_id == course.id))
        .scalars()
        .all()
    )
    exceptions = list(
        db.execute(
            select(CalendarException)
            .where(CalendarException.course_id == course.id)
            .order_by(CalendarException.date)
        )
        .scalars()
        .all()
    )
    topic_count = int(
        db.execute(
            select(func.count())
            .select_from(Topic)
            .join(SyllabusUnit, Topic.unit_id == SyllabusUnit.id)
            .where(
                SyllabusUnit.course_id == course.id,
                SyllabusUnit.archived_at.is_(None),
                Topic.archived_at.is_(None),
            )
        ).scalar_one()
    )
    unit_count = int(
        db.execute(
            select(func.count())
            .select_from(SyllabusUnit)
            .where(
                SyllabusUnit.course_id == course.id,
                SyllabusUnit.archived_at.is_(None),
            )
        ).scalar_one()
    )
    has_approved = (
        db.execute(
            select(ScheduleRevision.id)
            .where(
                ScheduleRevision.course_id == course.id,
                ScheduleRevision.approval_status == ApprovalStatus.APPROVED,
            )
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )

    detail = CourseDetail.model_validate(
        {
            **CourseRead.model_validate(course).model_dump(),
            "availability_rules": [
                AvailabilityRuleRead.model_validate(rule).model_dump() for rule in rules
            ],
            "exceptions": [
                CalendarExceptionRead.model_validate(item).model_dump() for item in exceptions
            ],
            "unit_count": unit_count,
            "topic_count": topic_count,
            "lecture_slots_per_week": lecture_slots_per_week(
                rules, course.lecture_duration_minutes
            ),
            "has_approved_schedule": has_approved,
        }
    )
    return detail


def course_with_structure(db: Session, course_id: uuid.UUID) -> Course:
    course = db.execute(
        select(Course)
        .where(Course.id == course_id)
        .options(selectinload(Course.units).selectinload(SyllabusUnit.topics))
    ).scalar_one_or_none()
    if course is None:
        raise NotFoundError("Course not found.", code="COURSE_NOT_FOUND")
    return course


def delete_course(db: Session, *, course: Course, actor_id: uuid.UUID) -> None:
    """Delete a course and all related units, topics, sessions, revisions, and files."""

    from app.core.config import settings

    syllabi_dir = settings.storage_dir / "syllabi" / str(course.id)
    if syllabi_dir.exists():
        import shutil

        shutil.rmtree(syllabi_dir, ignore_errors=True)

    db.delete(course)
    db.flush()
