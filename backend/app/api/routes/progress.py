"""Dashboard, coverage, and calendar routes."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CourseDep, DbSession
from app.core.timeutil import now_utc
from app.schemas.progress import CalendarResponse, DashboardSummary, ProgressDetailResponse
from app.services import progress as progress_service

router = APIRouter(prefix="/courses/{course_id}", tags=["progress"])


@router.get(
    "/dashboard",
    response_model=DashboardSummary,
    summary="Course dashboard: coverage, lectures, at-risk topics, projected completion",
)
def dashboard(course: CourseDep, db: DbSession) -> DashboardSummary:
    return progress_service.build_dashboard(db, course=course)


@router.get(
    "/progress",
    response_model=ProgressDetailResponse,
    summary="Per-topic and per-unit progress",
)
def progress(course: CourseDep, db: DbSession) -> ProgressDetailResponse:
    summary = progress_service.build_dashboard(db, course=course)
    topics, units = progress_service.topic_progress_rows(db, course=course)
    return ProgressDetailResponse(summary=summary, topics=topics, units=units)


@router.get(
    "/calendar",
    response_model=CalendarResponse,
    summary="Calendar day summaries for a date range",
)
def calendar(
    course: CourseDep,
    db: DbSession,
    view: Annotated[str, Query(pattern="^(day|week|semester)$")] = "week",
    range_start: Annotated[date | None, Query()] = None,
    range_end: Annotated[date | None, Query()] = None,
) -> CalendarResponse:
    today = now_utc().date()
    start = range_start
    end = range_end
    if start is None:
        if view == "day":
            start = today
        elif view == "week":
            start = today - timedelta(days=today.weekday())
        else:
            start = course.start_date
    if end is None:
        if view == "day":
            end = start
        elif view == "week":
            end = start + timedelta(days=6)
        else:
            end = course.end_date
    return progress_service.build_calendar(
        db, course=course, range_start=start, range_end=end, view=view
    )
