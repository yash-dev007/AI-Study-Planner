"""Lecture status updates and missed-lecture recovery schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.entities import SessionStatus
from app.schemas.schedule import LectureRead, RecoveryStrategy, RevisionSummary, ScheduleRead


class DeliveredMinutes(BaseModel):
    topic_id: uuid.UUID
    minutes: int = Field(ge=0, le=600)


class LectureStatusUpdateRequest(BaseModel):
    """Mark a lecture completed, missed, or cancelled.

    ``delivered_minutes`` records partial coverage: a 90-minute topic with 30
    minutes actually taught keeps 60 minutes of remaining work. When omitted, a
    completed lecture is assumed to have covered everything it was planned for.
    """

    status: Literal["scheduled", "completed", "missed", "cancelled"]
    delivered_minutes: list[DeliveredMinutes] | None = None
    reason: str = Field(default="", max_length=500)
    recalculate: bool = Field(
        default=True,
        description="Recalculate the remaining schedule. The result is a new revision "
        "awaiting approval; it is never published silently.",
    )
    strategy: RecoveryStrategy = "preserve_future"
    base_revision_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_status(self) -> LectureStatusUpdateRequest:
        if self.status == "completed" and self.delivered_minutes is None:
            return self
        if self.status in {"missed", "cancelled"} and self.delivered_minutes:
            raise ValueError(
                "A missed or cancelled lecture cannot report delivered minutes. "
                "Mark it completed if part of it was taught."
            )
        if self.status == "scheduled" and self.delivered_minutes:
            raise ValueError("Delivered minutes can only be reported for a completed lecture.")
        return self


class LectureStatusUpdateResponse(BaseModel):
    lecture: LectureRead
    revision: RevisionSummary | None = Field(
        default=None, description="The newly proposed revision, when a recalculation ran."
    )
    schedule: ScheduleRead | None = None
    message: str = ""
    requires_approval: bool = False


class LectureTopicProgressRead(BaseModel):
    """How much of a topic this lecture teaches, and how much is left of it."""

    topic_id: uuid.UUID
    title: str
    allocated_minutes: int
    estimated_minutes: int
    delivered_minutes_total: int
    remaining_minutes: int
    completion_status: str


class LectureDetailResponse(BaseModel):
    lecture: LectureRead
    course_id: uuid.UUID
    course_name: str
    revision_number: int | None = None
    revision_status: str | None = None
    max_minutes: int = 0
    remaining_minutes_by_topic: dict[uuid.UUID, int] = Field(default_factory=dict)
    topic_progress: list[LectureTopicProgressRead] = Field(default_factory=list)
    last_change: datetime | None = None
    last_change_reason: str = ""


class CalendarEntry(BaseModel):
    lecture_id: uuid.UUID
    sequence: int
    date: str
    scheduled_start: datetime
    scheduled_end: datetime
    status: SessionStatus
    topics: list[str] = Field(default_factory=list)
    minutes: int = 0
    is_published: bool = True
    note: str = ""
    change_reason: str = ""
