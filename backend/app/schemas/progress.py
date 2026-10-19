"""Dashboard, progress, and calendar schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.entities import ApprovalStatus, RevisionTrigger, SessionStatus


class TopicProgressRead(BaseModel):
    topic_id: uuid.UUID
    unit_id: uuid.UUID
    unit_title: str
    unit_sequence: int
    sequence_number: int
    title: str
    estimated_minutes: int
    delivered_minutes: int
    remaining_minutes: int
    progress_percent: float
    state: str = Field(
        description="completed | in_progress | pending | at_risk | unplaced | not_scheduled"
    )
    needs_review: bool = False
    sessions_planned: int = 0
    first_scheduled: datetime | None = None
    last_scheduled: datetime | None = None
    prerequisites_met: bool = True
    blocked_by: list[str] = Field(default_factory=list)
    reason: str = ""


class UnitProgressRead(BaseModel):
    unit_id: uuid.UUID
    title: str
    sequence_number: int
    topic_count: int
    completed_topics: int
    in_progress_topics: int
    pending_topics: int
    at_risk_topics: int
    estimated_minutes: int
    delivered_minutes: int
    remaining_minutes: int
    coverage_percent: float


class CalendarDayRead(BaseModel):
    date: date
    weekday: int
    is_blocked: bool = False
    block_reason: str = ""
    lectures: int = 0
    minutes: int = 0
    statuses: list[SessionStatus] = Field(default_factory=list)


class UpcomingLectureRead(BaseModel):
    lecture_id: uuid.UUID
    sequence: int
    scheduled_start: datetime
    scheduled_end: datetime
    topics: list[str] = Field(default_factory=list)
    minutes: int = 0
    status: SessionStatus = SessionStatus.SCHEDULED
    minutes_remaining_in_topics: int = 0


class ScheduleStateRead(BaseModel):
    revision_id: uuid.UUID | None = None
    revision_number: int | None = None
    approval_status: ApprovalStatus | None = None
    trigger_type: RevisionTrigger | None = None
    approved_at: datetime | None = None
    published_at: datetime | None = None
    has_pending_revision: bool = False
    pending_revision_id: uuid.UUID | None = None
    pending_revision_number: int | None = None


class DashboardSummary(BaseModel):
    course_id: uuid.UUID
    course_name: str
    as_of: datetime
    schedule: ScheduleStateRead
    total_units: int = 0
    total_topics: int = 0
    completed_topics: int = 0
    in_progress_topics: int = 0
    pending_topics: int = 0
    at_risk_topics: int = 0
    unplaced_topics: int = 0
    total_estimated_minutes: int = 0
    delivered_minutes: int = 0
    planned_minutes: int = 0
    remaining_minutes: int = 0
    coverage_percent: float = 0.0
    planned_lectures: int = 0
    completed_lectures: int = 0
    missed_lectures: int = 0
    cancelled_lectures: int = 0
    upcoming_lectures: int = 0
    rescheduled_lectures: int = 0
    capacity_sessions_remaining: int = 0
    sessions_required_remaining: int = 0
    additional_sessions_required: int = 0
    estimated_completion: datetime | None = None
    semester_end: date | None = None
    days_remaining: int = 0
    on_track: bool = True
    warnings: list[str] = Field(default_factory=list)
    next_lectures: list[UpcomingLectureRead] = Field(default_factory=list)
    at_risk: list[TopicProgressRead] = Field(default_factory=list)
    units: list[UnitProgressRead] = Field(default_factory=list)


class ProgressDetailResponse(BaseModel):
    summary: DashboardSummary
    topics: list[TopicProgressRead]
    units: list[UnitProgressRead]


class CalendarResponse(BaseModel):
    course_id: uuid.UUID
    view: str
    range_start: date
    range_end: date
    days: list[CalendarDayRead]
