"""Course configuration schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.entities import CourseStatus, ExceptionType

DEFAULT_TIMEZONE = "UTC"


class AvailabilityRuleInput(BaseModel):
    weekday: int = Field(ge=0, le=6, description="Monday=0 ... Sunday=6")
    start_time: time
    end_time: time
    room: str = Field(default="", max_length=100)
    effective_from: date | None = None
    effective_until: date | None = None
    is_enabled: bool = True

    @model_validator(mode="after")
    def validate_window(self) -> AvailabilityRuleInput:
        if self.end_time <= self.start_time:
            raise ValueError("The end time must be after the start time.")
        if (
            self.effective_from
            and self.effective_until
            and self.effective_until < self.effective_from
        ):
            raise ValueError("effective_until cannot precede effective_from.")
        return self


class AvailabilityRuleRead(AvailabilityRuleInput):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID


class CalendarExceptionInput(BaseModel):
    date: date
    exception_type: ExceptionType
    description: str = Field(default="", max_length=300)
    blocks_teaching: bool = True


class CalendarExceptionRead(CalendarExceptionInput):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID


class CourseSettings(BaseModel):
    """Course-level behaviour switches."""

    auto_publish_reschedules: bool = Field(
        default=False,
        description=(
            "When true, an approved-by-default recalculation is published immediately. "
            "When false (default) every recalculation waits for the professor's approval."
        ),
    )
    preserve_future_lectures: bool = Field(
        default=True,
        description="Prefer keeping unchanged future lectures in place during recovery.",
    )
    combine_short_topics: bool = Field(
        default=True, description="Allow several short topics to share one lecture slot."
    )
    min_allocation_minutes: int = Field(default=5, ge=5, le=60)
    notes: str = Field(default="", max_length=2000)


class CourseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    course_code: str | None = Field(default=None, max_length=50)
    department: str = Field(default="", max_length=150)
    semester: str = Field(default="", max_length=50)
    academic_year: str = Field(default="", max_length=20)
    start_date: date
    end_date: date
    target_completion_date: date | None = None
    total_available_minutes: int | None = Field(default=None, gt=0)
    timezone: str = DEFAULT_TIMEZONE
    lecture_duration_minutes: int = Field(default=60, ge=15, le=300)
    total_weeks: int = Field(default=16, ge=1, le=52)
    availability_rules: list[AvailabilityRuleInput] = Field(default_factory=list)
    exceptions: list[CalendarExceptionInput] = Field(default_factory=list)
    settings: CourseSettings = Field(default_factory=CourseSettings)

    @model_validator(mode="after")
    def validate_dates(self) -> CourseCreate:
        if self.end_date < self.start_date:
            raise ValueError("The semester end date must not precede the start date.")
        if self.target_completion_date is not None and not (
            self.start_date <= self.target_completion_date <= self.end_date
        ):
            raise ValueError("The target completion date must fall within the semester.")
        return self


class CourseUpdate(BaseModel):
    """Partial update. ``version`` guards against concurrent edits."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    course_code: str | None = Field(default=None, max_length=50)
    department: str | None = Field(default=None, max_length=150)
    semester: str | None = Field(default=None, max_length=50)
    academic_year: str | None = Field(default=None, max_length=20)
    start_date: date | None = None
    end_date: date | None = None
    target_completion_date: date | None = None
    total_available_minutes: int | None = Field(default=None, gt=0)
    timezone: str | None = None
    lecture_duration_minutes: int | None = Field(default=None, ge=15, le=300)
    total_weeks: int | None = Field(default=None, ge=1, le=52)
    status: CourseStatus | None = None
    settings: CourseSettings | None = None
    version: int | None = Field(
        default=None, description="Required to update a course that already has a schedule."
    )


class CourseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    professor_id: uuid.UUID
    name: str
    course_code: str | None
    department: str
    semester: str
    academic_year: str
    start_date: date
    end_date: date
    target_completion_date: date | None = None
    total_available_minutes: int | None = None
    timezone: str
    status: CourseStatus
    lecture_duration_minutes: int
    total_weeks: int
    version: int
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class CourseDetail(CourseRead):
    availability_rules: list[AvailabilityRuleRead] = Field(default_factory=list)
    exceptions: list[CalendarExceptionRead] = Field(default_factory=list)
    unit_count: int = 0
    topic_count: int = 0
    lecture_slots_per_week: int = 0
    has_approved_schedule: bool = False
