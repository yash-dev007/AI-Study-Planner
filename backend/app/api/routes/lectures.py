"""Lecture routes: list, inspect, and mark lectures, triggering recovery."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CourseDep, CurrentUser, DbSession, Pagination
from app.core.timeutil import ensure_utc
from app.models.entities import SessionStatus
from app.schemas.common import Page
from app.schemas.lecture import (
    CalendarEntry,
    LectureDetailResponse,
    LectureStatusUpdateRequest,
    LectureStatusUpdateResponse,
)
from app.schemas.schedule import LectureRead
from app.services import lectures as lecture_service
from app.services import progress as progress_service
from app.services import scheduling as scheduling_service

router = APIRouter(prefix="/courses/{course_id}/lectures", tags=["lectures"])


@router.get(
    "/calendar/entries",
    response_model=list[CalendarEntry],
    summary="Calendar entries between two dates (day, week, or semester view)",
)
def calendar_entries(
    course: CourseDep,
    db: DbSession,
    range_start: Annotated[date, Query(description="Inclusive start date")],
    range_end: Annotated[date, Query(description="Inclusive end date")],
) -> list[CalendarEntry]:
    rows = progress_service.calendar_entries(
        db, course=course, range_start=range_start, range_end=range_end
    )
    return [CalendarEntry.model_validate(row) for row in rows]


@router.get("", response_model=Page[LectureRead], summary="Lectures of the current timetable")
def list_lectures(
    course: CourseDep,
    db: DbSession,
    pagination: Pagination,
    lecture_status: Annotated[SessionStatus | None, Query(alias="status")] = None,
    from_date: Annotated[date | None, Query()] = None,
    to_date: Annotated[date | None, Query()] = None,
    revision_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[LectureRead]:
    limit, offset = pagination
    revision = (
        scheduling_service.get_revision(db, course, revision_id)
        if revision_id
        else scheduling_service.base_revision_for(db, course)
    )
    if revision is None:
        return Page.build([], total=0, limit=limit, offset=offset)

    filtered = []
    for session in scheduling_service.revision_sessions(db, revision):
        if lecture_status is not None and session.status != lecture_status:
            continue
        day = ensure_utc(session.scheduled_start).date()
        if from_date is not None and day < from_date:
            continue
        if to_date is not None and day > to_date:
            continue
        filtered.append(session)

    window = filtered[offset : offset + limit]
    return Page.build(
        [lecture_service.lecture_read(session) for session in window],
        total=len(filtered),
        limit=limit,
        offset=offset,
    )


@router.get("/{lecture_id}", response_model=LectureDetailResponse, summary="Lecture details")
def get_lecture(lecture_id: uuid.UUID, course: CourseDep, db: DbSession) -> LectureDetailResponse:
    data = lecture_service.lecture_detail(db, course=course, lecture_id=lecture_id)
    session = data["lecture"]
    revision = data["revision"]
    topic_progress = data["topic_progress"]
    return LectureDetailResponse(
        lecture=lecture_service.lecture_read(session),
        course_id=course.id,
        course_name=course.name,
        revision_number=revision.revision_number if revision else None,
        revision_status=str(revision.approval_status) if revision else None,
        max_minutes=course.lecture_duration_minutes,
        remaining_minutes_by_topic={
            item.topic_id: item.remaining_minutes for item in topic_progress
        },
        topic_progress=topic_progress,
        last_change=data["last_change_at"],
        last_change_reason=data["last_change_reason"],
    )


@router.patch(
    "/{lecture_id}/status",
    response_model=LectureStatusUpdateResponse,
    summary="Mark a lecture completed, missed, or cancelled and recalculate",
)
def update_status(
    lecture_id: uuid.UUID,
    course: CourseDep,
    payload: LectureStatusUpdateRequest,
    db: DbSession,
    user: CurrentUser,
) -> LectureStatusUpdateResponse:
    session, revision, warnings, message = lecture_service.update_lecture_status(
        db, course=course, user=user, lecture_id=lecture_id, payload=payload
    )
    db.commit()
    schedule = (
        scheduling_service.build_schedule_read(db, course=course, revision=revision)
        if revision is not None
        else None
    )
    db.refresh(session)
    if warnings:
        message = f"{message} {' '.join(warnings)}"
    return LectureStatusUpdateResponse(
        lecture=lecture_service.lecture_read(session),
        revision=scheduling_service.revision_summary(revision) if revision else None,
        schedule=schedule,
        message=message,
        requires_approval=bool(schedule.requires_approval) if schedule else False,
    )
