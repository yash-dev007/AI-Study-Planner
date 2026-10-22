"""Course configuration routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CourseDep, CurrentUser, DbSession, Pagination
from app.models.entities import CourseStatus
from app.schemas.common import MessageResponse, Page
from app.schemas.course import (
    AvailabilityRuleInput,
    AvailabilityRuleRead,
    CalendarExceptionInput,
    CalendarExceptionRead,
    CourseCreate,
    CourseDetail,
    CourseRead,
    CourseUpdate,
)
from app.services import courses as course_service
from app.services.audit import record_audit

router = APIRouter(prefix="/courses", tags=["courses"])


@router.post(
    "",
    response_model=CourseDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a course",
)
def create_course(payload: CourseCreate, db: DbSession, user: CurrentUser) -> CourseDetail:
    course = course_service.create_course(db, professor=user, payload=payload, actor_id=user.id)
    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="course.created",
        entity_type="course",
        entity_id=str(course.id),
        detail={"name": course.name, "code": course.course_code},
    )
    db.commit()
    return course_service.course_detail(db, course)


@router.get("", response_model=Page[CourseRead], summary="List my courses")
def list_courses(
    db: DbSession,
    user: CurrentUser,
    pagination: Pagination,
    course_status: Annotated[CourseStatus | None, Query(alias="status")] = None,
    search: str | None = None,
) -> Page[CourseRead]:
    limit, offset = pagination
    rows, total = course_service.list_courses(
        db, user=user, status=course_status, search=search, limit=limit, offset=offset
    )
    return Page.build(
        [CourseRead.model_validate(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{course_id}", response_model=CourseDetail, summary="Course configuration")
def get_course(course: CourseDep, db: DbSession) -> CourseDetail:
    return course_service.course_detail(db, course)


@router.patch(
    "/{course_id}",
    response_model=CourseDetail,
    summary="Update course configuration (recalculate afterwards)",
)
def update_course(
    course: CourseDep, payload: CourseUpdate, db: DbSession, user: CurrentUser
) -> CourseDetail:
    updated = course_service.update_course(db, course=course, payload=payload, actor_id=user.id)
    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="course.updated",
        entity_type="course",
        entity_id=str(course.id),
        detail={"fields": sorted(payload.model_dump(exclude_unset=True, exclude={"version"}))},
    )
    db.commit()
    return course_service.course_detail(db, updated)


@router.delete(
    "/{course_id}",
    response_model=MessageResponse,
    summary="Delete a course and all associated data",
)
def delete_course(
    course: CourseDep, db: DbSession, user: CurrentUser
) -> MessageResponse:
    course_name = course.name
    course_id = course.id
    course_service.delete_course(db, course=course, actor_id=user.id)
    record_audit(
        db,
        user_id=user.id,
        course_id=None,
        action="course.deleted",
        entity_type="course",
        entity_id=str(course_id),
        detail={"name": course_name},
    )
    db.commit()
    return MessageResponse(message=f"Course '{course_name}' and all associated data deleted.")


@router.put(
    "/{course_id}/availability",
    response_model=list[AvailabilityRuleRead],
    summary="Replace the weekly teaching windows",
)
def replace_availability(
    course: CourseDep,
    payload: list[AvailabilityRuleInput],
    db: DbSession,
    user: CurrentUser,
) -> list[AvailabilityRuleRead]:
    rules = course_service.replace_availability_rules(db, course=course, rules=payload)
    course.version += 1
    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="course.availability_replaced",
        entity_type="course",
        entity_id=str(course.id),
        detail={"rule_count": len(rules)},
    )
    db.commit()
    return [AvailabilityRuleRead.model_validate(rule) for rule in rules]


@router.post(
    "/{course_id}/exceptions",
    response_model=CalendarExceptionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a holiday, exam, event, or unavailable day",
)
def add_exception(
    course: CourseDep, payload: CalendarExceptionInput, db: DbSession, user: CurrentUser
) -> CalendarExceptionRead:
    row = course_service.add_exception(db, course=course, payload=payload)
    course.version += 1
    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="course.exception_added",
        entity_type="calendar_exception",
        entity_id=str(row.id),
        detail={"date": row.date.isoformat(), "type": str(row.exception_type)},
    )
    db.commit()
    return CalendarExceptionRead.model_validate(row)


@router.delete(
    "/{course_id}/exceptions/{exception_id}",
    response_model=MessageResponse,
    summary="Remove a calendar exception",
)
def delete_exception(
    course: CourseDep, exception_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> MessageResponse:
    course_service.delete_exception(db, course=course, exception_id=exception_id)
    course.version += 1
    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="course.exception_removed",
        entity_type="calendar_exception",
        entity_id=str(exception_id),
    )
    db.commit()
    return MessageResponse(message="Calendar exception removed.")
