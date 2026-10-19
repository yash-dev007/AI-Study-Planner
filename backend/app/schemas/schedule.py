"""Schedule generation, review, approval, and revision schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.entities import (
    AllocationStatus,
    ApprovalStatus,
    RevisionTrigger,
    SessionStatus,
)

RecoveryStrategy = Literal["preserve_future", "redistribute_all"]


class AllocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    topic_id: uuid.UUID
    topic_title: str = ""
    unit_title: str = ""
    planned_minutes: int
    sequence_in_session: int
    completion_status: AllocationStatus
    actual_minutes: int | None = None


class LectureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    revision_id: uuid.UUID
    sequence: int
    scheduled_start: datetime
    scheduled_end: datetime
    status: SessionStatus
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    cancellation_reason: str = ""
    notes: str = ""
    is_published: bool
    allocations: list[AllocationRead] = Field(default_factory=list)


class UnplacedTopicRead(BaseModel):
    topic_id: uuid.UUID
    title: str
    remaining_minutes: int
    reason: str


class ValidationIssueRead(BaseModel):
    code: str
    message: str
    severity: str
    topic_id: uuid.UUID | None = None
    slot_index: int | None = None


class CapacitySummary(BaseModel):
    sessions_available: int = 0
    sessions_used: int = 0
    capacity_minutes: int = 0
    scheduled_minutes: int = 0
    completed_minutes: int = 0
    remaining_minutes: int = 0
    utilization_percent: float = 0.0
    coverage_percent: float = 0.0
    projected_coverage_percent: float = 0.0
    additional_sessions_required: int = 0
    expected_completion: datetime | None = None
    is_feasible: bool = True


class DiffChangeRead(BaseModel):
    change_type: str
    topic_id: uuid.UUID | None = None
    topic_title: str = ""
    previous_date: datetime | None = None
    new_date: datetime | None = None
    previous_minutes: int | None = None
    new_minutes: int | None = None
    change_reason: str = ""


class DiffSummary(BaseModel):
    summary: str = ""
    added_count: int = 0
    removed_count: int = 0
    moved_count: int = 0
    changes: list[DiffChangeRead] = Field(default_factory=list)


class RevisionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    revision_number: int
    trigger_type: RevisionTrigger
    approval_status: ApprovalStatus
    change_summary: str
    explanation: dict[str, Any]
    warnings: list[str]
    metrics: dict[str, Any]
    created_by: uuid.UUID
    approved_by: uuid.UUID | None = None
    approved_at: datetime | None = None
    published_at: datetime | None = None
    base_revision_id: uuid.UUID | None = None
    created_at: datetime


class RevisionChangeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    revision_id: uuid.UUID
    lecture_session_id: uuid.UUID | None = None
    topic_id: uuid.UUID | None = None
    change_type: str
    previous_date: date | None = None
    new_date: date | None = None
    previous_minutes: int | None = None
    new_minutes: int | None = None
    change_reason: str


class ScheduleRead(BaseModel):
    """A proposed or approved schedule with everything needed to review it."""

    revision: RevisionSummary | None = None
    lectures: list[LectureRead] = Field(default_factory=list)
    unplaced_topics: list[UnplacedTopicRead] = Field(default_factory=list)
    validation_issues: list[ValidationIssueRead] = Field(default_factory=list)
    diff: DiffSummary | None = None
    capacity: CapacitySummary = Field(default_factory=CapacitySummary)
    requires_approval: bool = False


class GenerateScheduleRequest(BaseModel):
    strategy: RecoveryStrategy = "preserve_future"
    auto_publish: bool | None = Field(
        default=None,
        description="Overrides the course setting for this run. Publishing is always explicit.",
    )


class RecalculateRequest(GenerateScheduleRequest):
    trigger: RevisionTrigger = RevisionTrigger.COURSE_CONFIG_CHANGED
    base_revision_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "The revision the recalculation is based on. When supplied and no longer the "
            "current revision, the request is rejected with 409 so concurrent edits cannot "
            "silently overwrite each other."
        ),
    )
    reason: str = Field(default="", max_length=500)


class ApproveRequest(BaseModel):
    note: str = Field(default="", max_length=500)
    publish: bool = True


class RollbackRequest(BaseModel):
    reason: str = Field(default="", max_length=500)


class RecalculateResponse(ScheduleRead):
    pass


class DiffResponse(BaseModel):
    revision: RevisionSummary
    base_revision: RevisionSummary | None = None
    diff: DiffSummary
