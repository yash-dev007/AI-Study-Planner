"""Lecture status updates and missed-lecture recovery.

Marking a lecture missed, cancelled, or completed always records what really
happened. A recalculation then produces a *proposal*: the remaining topics are
redistributed, already delivered work is never rescheduled, and prerequisite
order is re-validated. Nothing is published until the professor approves.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.timeutil import ensure_utc, now_utc
from app.models.entities import (
    AllocationStatus,
    ApprovalStatus,
    Course,
    LectureSession,
    RevisionTrigger,
    ScheduleRevision,
    SessionStatus,
    TopicAllocation,
    User,
)
from app.schemas.lecture import LectureStatusUpdateRequest, LectureTopicProgressRead
from app.schemas.schedule import AllocationRead, LectureRead
from app.services.audit import record_audit
from app.services.scheduling import (
    base_revision_for,
    delivered_minutes,
    generate_schedule,
    revision_sessions,
)

EDITABLE_STATUSES = {
    "scheduled": SessionStatus.SCHEDULED,
    "completed": SessionStatus.COMPLETED,
    "missed": SessionStatus.MISSED,
    "cancelled": SessionStatus.CANCELLED,
}


def get_lecture(db: Session, *, course: Course, lecture_id: uuid.UUID) -> LectureSession:
    session = db.get(LectureSession, lecture_id)
    if session is None or session.course_id != course.id:
        raise NotFoundError("Lecture not found.", code="LECTURE_NOT_FOUND")
    return session


def _load_allocations(db: Session, session: LectureSession) -> list[TopicAllocation]:
    return list(
        db.execute(
            select(TopicAllocation)
            .where(TopicAllocation.lecture_session_id == session.id)
            .order_by(TopicAllocation.sequence_in_session)
            .options(selectinload(TopicAllocation.topic))
        )
        .scalars()
        .all()
    )


def _ensure_current_revision(
    db: Session, *, course: Course, session: LectureSession
) -> ScheduleRevision:
    """Only the current timetable may be edited."""

    revision = db.get(ScheduleRevision, session.revision_id)
    if revision is None:
        raise NotFoundError("Schedule revision not found.", code="REVISION_NOT_FOUND")
    base = base_revision_for(db, course)
    if base is None or base.id != revision.id:
        raise ConflictError(
            "This lecture belongs to an older revision of the schedule. Open the current "
            "schedule and update the lecture there.",
            code="REVISION_NOT_CURRENT",
            details={
                "lecture_revision_id": str(revision.id),
                "current_revision_id": str(base.id) if base else None,
            },
        )
    return revision


def _apply_delivered_minutes(
    session: LectureSession,
    allocations: list[TopicAllocation],
    delivered: dict[uuid.UUID, int],
) -> list[str]:
    warnings: list[str] = []
    known = {allocation.topic_id for allocation in allocations}
    unknown = [topic_id for topic_id in delivered if topic_id not in known]
    if unknown:
        raise ValidationFailedError(
            "Delivered minutes were reported for a topic that is not part of this lecture.",
            code="UNKNOWN_TOPIC_IN_LECTURE",
            details={"topic_ids": [str(item) for item in unknown]},
        )

    for allocation in allocations:
        planned = allocation.allocated_duration_minutes
        actual = delivered.get(allocation.topic_id)
        if actual is None:
            allocation.actual_duration_minutes = planned
            allocation.completion_status = AllocationStatus.COMPLETED
            continue
        allocation.actual_duration_minutes = actual
        if actual == 0:
            allocation.completion_status = AllocationStatus.SKIPPED
        elif actual >= planned:
            allocation.completion_status = AllocationStatus.COMPLETED
        else:
            allocation.completion_status = AllocationStatus.IN_PROGRESS
            warnings.append(
                f"Only {actual} of {planned} planned minutes were taught. The remainder stays "
                "in the plan."
            )
    return warnings


def _reset_allocations(allocations: list[TopicAllocation]) -> None:
    for allocation in allocations:
        allocation.completion_status = AllocationStatus.PLANNED
        allocation.actual_duration_minutes = None


def _remaining_work_in_session(session: LectureSession, allocations: list[TopicAllocation]) -> int:
    if session.status != SessionStatus.COMPLETED:
        return sum(allocation.allocated_duration_minutes for allocation in allocations)
    remaining = 0
    for allocation in allocations:
        delivered = allocation.actual_duration_minutes or 0
        if allocation.completion_status == AllocationStatus.SKIPPED:
            delivered = 0
        remaining += max(0, allocation.allocated_duration_minutes - delivered)
    return remaining


def update_lecture_status(
    db: Session,
    *,
    course: Course,
    user: User,
    lecture_id: uuid.UUID,
    payload: LectureStatusUpdateRequest,
) -> tuple[LectureSession, ScheduleRevision | None, list[str], str]:
    session = get_lecture(db, course=course, lecture_id=lecture_id)
    revision = _ensure_current_revision(db, course=course, session=session)
    allocations = _load_allocations(db, session)
    warnings: list[str] = []
    target = EDITABLE_STATUSES[payload.status]

    if (
        target == session.status
        and target != SessionStatus.COMPLETED
        and not payload.delivered_minutes
    ):
        return session, None, warnings, "The lecture already has this status."

    delivered = (
        {item.topic_id: item.minutes for item in payload.delivered_minutes}
        if payload.delivered_minutes
        else {}
    )

    if target == SessionStatus.COMPLETED:
        if not allocations:
            raise ValidationFailedError(
                "This lecture has no planned topics, so it cannot be completed.",
                code="LECTURE_HAS_NO_TOPICS",
            )
        warnings.extend(_apply_delivered_minutes(session, allocations, delivered))
        session.status = SessionStatus.COMPLETED
        session.actual_start = session.actual_start or ensure_utc(session.scheduled_start)
        taught = 0
        for allocation in allocations:
            if allocation.completion_status == AllocationStatus.SKIPPED:
                continue
            taught += allocation.actual_duration_minutes or 0
        session.actual_end = session.actual_start + timedelta(
            minutes=taught or course.lecture_duration_minutes
        )
        session.cancellation_reason = ""
    elif target in {SessionStatus.MISSED, SessionStatus.CANCELLED}:
        session.status = target
        session.cancellation_reason = payload.reason or session.cancellation_reason
        session.actual_start = None
        session.actual_end = None
        _reset_allocations(allocations)
    else:
        session.status = SessionStatus.SCHEDULED
        session.cancellation_reason = ""
        session.actual_start = None
        session.actual_end = None
        _reset_allocations(allocations)

    db.flush()

    trigger = {
        SessionStatus.COMPLETED: RevisionTrigger.LECTURE_COMPLETED,
        SessionStatus.MISSED: RevisionTrigger.LECTURE_MISSED,
        SessionStatus.CANCELLED: RevisionTrigger.LECTURE_MISSED,
    }.get(target)

    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action=f"lecture.{target.value}",
        entity_type="lecture_session",
        entity_id=str(session.id),
        detail={
            "revision_id": str(revision.id),
            "planned_minutes": sum(a.allocated_duration_minutes for a in allocations),
            "delivered": {str(key): value for key, value in delivered.items()},
            "reason": payload.reason,
        },
    )

    if not payload.recalculate or trigger is None:
        return session, None, warnings, "Lecture status saved."

    remaining = _remaining_work_in_session(session, allocations)
    if trigger == RevisionTrigger.LECTURE_COMPLETED and remaining == 0:
        return (
            session,
            None,
            warnings,
            "Lecture saved as completed. Everything planned for it was covered, so the "
            "remaining schedule does not need to change.",
        )

    reason = _recovery_reason(
        session=session, status=target, remaining=remaining, note=payload.reason
    )
    new_revision = generate_schedule(
        db,
        course=course,
        user=user,
        trigger=trigger,
        strategy=payload.strategy,
        reason=reason,
        base_revision_id=payload.base_revision_id,
        trigger_lecture_id=session.id,
    )
    summary = new_revision.change_summary
    warnings.extend(new_revision.warnings or [])
    return (
        session,
        new_revision,
        warnings,
        f"{summary} Review and approve revision {new_revision.revision_number} to publish it.",
    )


def _recovery_reason(
    *, session: LectureSession, status: SessionStatus, remaining: int, note: str
) -> str:
    stamp = ensure_utc(session.scheduled_start).strftime("%d %b %Y %H:%M")
    if status == SessionStatus.MISSED:
        base = f"Lecture {session.sequence} on {stamp} was marked as missed."
    elif status == SessionStatus.CANCELLED:
        base = f"Lecture {session.sequence} on {stamp} was cancelled."
    else:
        base = f"Lecture {session.sequence} on {stamp} was completed with partial coverage."
    if remaining:
        base += f" {remaining} minutes of teaching stayed pending and were redistributed."
    if note:
        base += f" Note: {note}"
    return base


class LectureDetail(TypedDict):
    """Everything the lecture screen needs, assembled in one place."""

    lecture: LectureSession
    allocations: list[TopicAllocation]
    revision: ScheduleRevision | None
    topic_progress: list[LectureTopicProgressRead]
    delivered: dict[uuid.UUID, int]
    last_change_at: datetime | None
    last_change_reason: str
    is_current: bool


def lecture_detail(db: Session, *, course: Course, lecture_id: uuid.UUID) -> LectureDetail:
    session = get_lecture(db, course=course, lecture_id=lecture_id)
    allocations = _load_allocations(db, session)
    revision = db.get(ScheduleRevision, session.revision_id)
    sessions = revision_sessions(db, revision) if revision else []
    delivered = delivered_minutes(sessions)

    topic_progress: list[LectureTopicProgressRead] = []
    for allocation in allocations:
        topic = allocation.topic
        if topic is None:
            continue
        done = delivered.get(topic.id, 0)
        topic_progress.append(
            LectureTopicProgressRead(
                topic_id=topic.id,
                title=topic.title,
                allocated_minutes=allocation.allocated_duration_minutes,
                estimated_minutes=topic.estimated_duration_minutes,
                delivered_minutes_total=done,
                remaining_minutes=max(0, topic.estimated_duration_minutes - done),
                completion_status=allocation.completion_status.value,
            )
        )

    last_change = db.execute(
        select(ScheduleRevision)
        .where(ScheduleRevision.course_id == course.id)
        .order_by(ScheduleRevision.revision_number.desc())
        .limit(1)
    ).scalar_one_or_none()

    base = base_revision_for(db, course)
    return {
        "lecture": session,
        "allocations": allocations,
        "revision": revision,
        "topic_progress": topic_progress,
        "delivered": delivered,
        "last_change_at": last_change.created_at if last_change else None,
        "last_change_reason": last_change.change_summary if last_change else "",
        "is_current": bool(revision is not None and base is not None and base.id == revision.id),
    }


def lecture_read(session: LectureSession) -> LectureRead:
    """Serialize a lecture with its topic titles for the UI."""

    return LectureRead(
        id=session.id,
        revision_id=session.revision_id,
        sequence=session.sequence,
        scheduled_start=ensure_utc(session.scheduled_start),
        scheduled_end=ensure_utc(session.scheduled_end),
        status=session.status,
        actual_start=ensure_utc(session.actual_start) if session.actual_start else None,
        actual_end=ensure_utc(session.actual_end) if session.actual_end else None,
        cancellation_reason=session.cancellation_reason,
        notes=session.notes,
        is_published=session.is_published,
        allocations=[
            AllocationRead(
                id=allocation.id,
                topic_id=allocation.topic_id,
                topic_title=allocation.topic.title if allocation.topic else "",
                allocated_duration_minutes=allocation.allocated_duration_minutes,
                sequence_in_session=allocation.sequence_in_session,
                completion_status=allocation.completion_status,
                actual_duration_minutes=allocation.actual_duration_minutes,
            )
            for allocation in sorted(session.allocations, key=lambda item: item.sequence_in_session)
        ],
    )


def revision_is_published(revision: ScheduleRevision | None) -> bool:
    return bool(
        revision is not None
        and revision.approval_status == ApprovalStatus.APPROVED
        and revision.published_at is not None
    )


def upcoming_lectures(
    db: Session, *, course: Course, revision: ScheduleRevision, limit: int = 5
) -> list[LectureSession]:
    reference = now_utc()
    sessions = [
        session
        for session in revision_sessions(db, revision)
        if session.status == SessionStatus.SCHEDULED
        and ensure_utc(session.scheduled_start) >= reference
    ]
    sessions.sort(key=lambda item: ensure_utc(item.scheduled_start))
    return sessions[:limit]
