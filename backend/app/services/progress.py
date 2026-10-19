"""Progress, coverage, and calendar read models for the dashboard.

Every number here is derived from persisted facts: topic estimates, delivered
minutes per topic, and the sessions of the current revision. Nothing is
estimated by the LLM.
"""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.timeutil import ensure_utc, now_utc
from app.domain.scheduling import CalendarBlock, count_available_sessions
from app.models.entities import (
    Course,
    LectureSession,
    SessionStatus,
)
from app.schemas.progress import (
    CalendarDayRead,
    CalendarResponse,
    DashboardSummary,
    ScheduleStateRead,
    TopicProgressRead,
    UnitProgressRead,
    UpcomingLectureRead,
)
from app.services.scheduling import (
    CourseState,
    availability_windows,
    base_revision_for,
    calendar_blocks,
    current_approved_revision,
    delivered_minutes,
    load_course_state,
    pending_revision,
    revision_sessions,
)

STATE_COMPLETED = "completed"
STATE_IN_PROGRESS = "in_progress"
STATE_PENDING = "pending"
STATE_AT_RISK = "at_risk"
STATE_UNPLACED = "unplaced"
STATE_NOT_SCHEDULED = "not_scheduled"


def _topic_states(
    state: CourseState, sessions: list[LectureSession], revision_explanation: dict
) -> tuple[list[TopicProgressRead], list[UnitProgressRead], dict[str, int]]:
    delivered = delivered_minutes(sessions)
    reference = now_utc()
    semester_end = state.course.end_date

    unplaced: dict[uuid.UUID, dict] = {}
    for item in (revision_explanation or {}).get("unplaced_topics", []):
        if not isinstance(item, dict):
            continue
        raw_id = item.get("topic_id")
        if raw_id is None:
            continue
        try:
            # Explanation payloads are stored as JSON, so ids come back as strings.
            unplaced[uuid.UUID(str(raw_id))] = item
        except ValueError:
            continue

    future_by_topic: dict[uuid.UUID, list[LectureSession]] = {}
    for session in sessions:
        if session.status != SessionStatus.SCHEDULED:
            continue
        if ensure_utc(session.scheduled_start) < reference:
            continue
        for allocation in session.allocations:
            future_by_topic.setdefault(allocation.topic_id, []).append(session)

    rows: list[TopicProgressRead] = []
    counters = {
        STATE_COMPLETED: 0,
        STATE_IN_PROGRESS: 0,
        STATE_PENDING: 0,
        STATE_AT_RISK: 0,
        STATE_UNPLACED: 0,
        STATE_NOT_SCHEDULED: 0,
    }

    for unit in state.units:
        for topic in unit.topics:
            done = min(delivered.get(topic.id, 0), topic.estimated_duration_minutes)
            remaining = max(0, topic.estimated_duration_minutes - done)
            planned_sessions = sorted(
                future_by_topic.get(topic.id, []), key=lambda item: ensure_utc(item.scheduled_start)
            )
            last_planned = (
                ensure_utc(planned_sessions[-1].scheduled_end) if planned_sessions else None
            )
            first_planned = (
                ensure_utc(planned_sessions[0].scheduled_start) if planned_sessions else None
            )

            prerequisites = state.prerequisites.get(topic.id, frozenset())
            blocked_by: list[str] = []
            for prerequisite_id in prerequisites:
                prerequisite = state.topics.get(prerequisite_id)
                if prerequisite is None:
                    continue
                if delivered.get(prerequisite_id, 0) < prerequisite.estimated_duration_minutes and (
                    remaining > 0 or done == 0
                ):
                    blocked_by.append(prerequisite.title)

            if remaining == 0:
                topic_state = STATE_COMPLETED
                reason = "All estimated teaching minutes were delivered."
            elif topic.id in unplaced:
                topic_state = STATE_UNPLACED
                reason = str(unplaced[topic.id].get("reason", "The topic could not be placed."))
            elif done > 0:
                topic_state = STATE_IN_PROGRESS
                reason = f"{done} of {topic.estimated_duration_minutes} minutes delivered."
            elif not planned_sessions:
                topic_state = STATE_NOT_SCHEDULED
                reason = "No remaining lecture covers this topic yet. Regenerate the schedule."
            else:
                topic_state = STATE_PENDING
                reason = "Scheduled and waiting to be taught."

            if topic_state in {STATE_PENDING, STATE_IN_PROGRESS} and (
                last_planned is not None and last_planned.date() > semester_end
            ):
                topic_state = STATE_AT_RISK
                reason = (
                    "The topic would finish after the semester ends. Add lectures or shorten "
                    "its estimated duration."
                )
            if blocked_by and topic_state == STATE_PENDING:
                reason = "Waiting for prerequisite(s): " + ", ".join(blocked_by) + "."

            counters[topic_state] += 1
            rows.append(
                TopicProgressRead(
                    topic_id=topic.id,
                    unit_id=unit.id,
                    unit_title=unit.title,
                    unit_sequence=unit.sequence_number,
                    sequence_number=topic.sequence_number,
                    title=topic.title,
                    estimated_minutes=topic.estimated_duration_minutes,
                    delivered_minutes=done,
                    remaining_minutes=remaining,
                    progress_percent=round(
                        (done / topic.estimated_duration_minutes * 100)
                        if topic.estimated_duration_minutes
                        else 100.0,
                        1,
                    ),
                    state=topic_state,
                    needs_review=topic.needs_review,
                    sessions_planned=len(planned_sessions),
                    first_scheduled=first_planned,
                    last_scheduled=last_planned,
                    prerequisites_met=not blocked_by,
                    blocked_by=blocked_by,
                    reason=reason,
                )
            )

    unit_rows: list[UnitProgressRead] = []
    for unit in state.units:
        unit_topics = [topic for topic in unit.topics]
        estimates = sum(topic.estimated_duration_minutes for topic in unit_topics)
        unit_delivered = sum(
            min(delivered.get(topic.id, 0), topic.estimated_duration_minutes)
            for topic in unit_topics
        )
        topic_rows = [row for row in rows if row.unit_id == unit.id]
        unit_rows.append(
            UnitProgressRead(
                unit_id=unit.id,
                title=unit.title,
                sequence_number=unit.sequence_number,
                topic_count=len(unit_topics),
                completed_topics=sum(1 for row in topic_rows if row.state == STATE_COMPLETED),
                in_progress_topics=sum(1 for row in topic_rows if row.state == STATE_IN_PROGRESS),
                pending_topics=sum(
                    1 for row in topic_rows if row.state in {STATE_PENDING, STATE_NOT_SCHEDULED}
                ),
                at_risk_topics=sum(
                    1 for row in topic_rows if row.state in {STATE_AT_RISK, STATE_UNPLACED}
                ),
                estimated_minutes=estimates,
                delivered_minutes=unit_delivered,
                remaining_minutes=max(0, estimates - unit_delivered),
                coverage_percent=round(
                    (unit_delivered / estimates * 100) if estimates else 100.0, 1
                ),
            )
        )
    return rows, unit_rows, counters


def build_dashboard(db: Session, *, course: Course, sessions_limit: int = 5) -> DashboardSummary:
    state = load_course_state(db, course)
    approved = current_approved_revision(db, course)
    pending = pending_revision(db, course)
    base = base_revision_for(db, course)
    sessions = revision_sessions(db, base) if base else []

    reference = now_utc()
    explanation = (base.explanation if base else {}) or {}

    topic_rows, unit_rows, counters = _topic_states(state, sessions, explanation)

    total_estimated = sum(topic.estimated_duration_minutes for topic in state.topics.values())
    delivered_total = sum(min(row.delivered_minutes, row.estimated_minutes) for row in topic_rows)
    remaining_total = max(0, total_estimated - delivered_total)

    completed_lectures = 0
    missed_lectures = 0
    cancelled_lectures = 0
    upcoming = 0
    planned_lectures = 0
    for session in sessions:
        if session.status == SessionStatus.COMPLETED:
            completed_lectures += 1
        elif session.status == SessionStatus.MISSED:
            missed_lectures += 1
        elif session.status == SessionStatus.CANCELLED:
            cancelled_lectures += 1
        elif ensure_utc(session.scheduled_start) >= reference:
            upcoming += 1
            planned_lectures += 1

    duration = max(15, course.lecture_duration_minutes)
    sessions_required = math.ceil(remaining_total / duration) if remaining_total else 0

    future_sessions = sorted(
        (
            session
            for session in sessions
            if session.status == SessionStatus.SCHEDULED
            and ensure_utc(session.scheduled_start) >= reference
        ),
        key=lambda item: ensure_utc(item.scheduled_start),
    )

    future_start = max(reference.date(), course.start_date)
    blocks = calendar_blocks(state) + [
        # Past and closed sessions still consume their slot, so they are excluded.
        _blocked_marker(session)
        for session in sessions
        if session.status != SessionStatus.SCHEDULED
        or ensure_utc(session.scheduled_start) < reference
    ]
    try:
        open_slots = count_available_sessions(
            start_date=future_start,
            end_date=course.end_date,
            windows=availability_windows(state),
            lecture_duration_minutes=duration,
            blocks=blocks,
        )
    except Exception:
        open_slots = 0

    # Already planned lectures hold slots inside their teaching windows, so the
    # free capacity is what is left after those are accounted for. The remaining
    # workload is covered by the planned lectures plus whatever still fits.
    capacity_remaining = max(0, open_slots - len(future_sessions))
    additional = max(0, sessions_required - len(future_sessions) - capacity_remaining)
    expected_completion = ensure_utc(future_sessions[-1].scheduled_end) if future_sessions else None

    warnings: list[str] = list(base.warnings or []) if base else []
    if counters[STATE_UNPLACED]:
        warnings.append(
            f"{counters[STATE_UNPLACED]} topic(s) could not be placed in the available calendar."
        )
    if additional:
        warnings.append(
            f"The remaining workload needs about {sessions_required} lecture(s). "
            f"{len(future_sessions)} are already planned and {capacity_remaining} slot(s) are "
            f"still free, so about {additional} extra session(s) are needed. Add availability, "
            "extend the semester, or accept a later finish."
        )
    if expected_completion and expected_completion.date() > course.end_date:
        warnings.append("The projected completion date is after the end of the semester.")
    if counters[STATE_NOT_SCHEDULED]:
        warnings.append(
            f"{counters[STATE_NOT_SCHEDULED]} topic(s) have no remaining lecture. Generate a "
            "schedule to cover them."
        )
    if pending is not None and (approved is None or pending.id != approved.id):
        warnings.append(
            f"Revision {pending.revision_number} is waiting for your review. The published "
            "timetable does not change until you approve it."
        )

    rescheduled = int((explanation.get("diff", {}) or {}).get("moved_count", 0))
    delivered_map = delivered_minutes(sessions)

    upcoming_rows = [
        UpcomingLectureRead(
            lecture_id=session.id,
            sequence=session.sequence,
            scheduled_start=ensure_utc(session.scheduled_start),
            scheduled_end=ensure_utc(session.scheduled_end),
            topics=[
                allocation.topic.title
                for allocation in session.allocations
                if allocation.topic is not None
            ],
            minutes=sum(
                allocation.allocated_duration_minutes for allocation in session.allocations
            ),
            status=session.status,
            minutes_remaining_in_topics=_minutes_remaining_in_topics(session, delivered_map),
        )
        for session in future_sessions[: max(1, sessions_limit)]
    ]

    at_risk_rows = [row for row in topic_rows if row.state in {STATE_AT_RISK, STATE_UNPLACED}]

    # Planner metrics are a snapshot of the moment the revision was created. They
    # are only used as a fallback for values that cannot be derived live: live
    # delivered minutes always win over the snapshot.
    if base is not None and expected_completion is None and base.metrics:
        projected = base.metrics.get("expected_completion")
        if projected:
            try:
                expected_completion = datetime.fromisoformat(str(projected))
            except ValueError:
                expected_completion = None

    return DashboardSummary(
        course_id=course.id,
        course_name=course.name,
        as_of=reference,
        schedule=ScheduleStateRead(
            revision_id=base.id if base else None,
            revision_number=base.revision_number if base else None,
            approval_status=base.approval_status if base else None,
            trigger_type=base.trigger_type if base else None,
            approved_at=base.approved_at if base else None,
            published_at=base.published_at if base else None,
            has_pending_revision=pending is not None
            and (approved is None or pending.id != approved.id),
            pending_revision_id=pending.id if pending is not None else None,
            pending_revision_number=pending.revision_number if pending is not None else None,
        ),
        total_units=len(state.units),
        total_topics=len(state.topics),
        completed_topics=counters[STATE_COMPLETED],
        in_progress_topics=counters[STATE_IN_PROGRESS],
        pending_topics=counters[STATE_PENDING] + counters[STATE_NOT_SCHEDULED],
        at_risk_topics=counters[STATE_AT_RISK] + counters[STATE_UNPLACED],
        unplaced_topics=counters[STATE_UNPLACED],
        total_estimated_minutes=total_estimated,
        delivered_minutes=delivered_total,
        planned_minutes=int((base.metrics or {}).get("scheduled_minutes", 0) or 0) if base else 0,
        remaining_minutes=remaining_total,
        coverage_percent=round(
            (delivered_total / total_estimated * 100) if total_estimated else 0.0, 1
        ),
        planned_lectures=planned_lectures,
        completed_lectures=completed_lectures,
        missed_lectures=missed_lectures,
        cancelled_lectures=cancelled_lectures,
        upcoming_lectures=upcoming,
        rescheduled_lectures=rescheduled,
        capacity_sessions_remaining=capacity_remaining,
        sessions_required_remaining=sessions_required,
        additional_sessions_required=additional,
        estimated_completion=expected_completion,
        semester_end=course.end_date,
        days_remaining=max(0, (course.end_date - reference.date()).days),
        on_track=additional == 0 and counters[STATE_UNPLACED] == 0,
        warnings=warnings,
        next_lectures=upcoming_rows,
        at_risk=at_risk_rows,
        units=unit_rows,
    )


def _minutes_remaining_in_topics(
    session: LectureSession, delivered_map: dict[uuid.UUID, int]
) -> int:
    """Minutes the topics of this lecture still need across the semester."""

    remaining = 0
    for allocation in session.allocations:
        estimated = allocation.topic.estimated_duration_minutes if allocation.topic else 0
        remaining += max(0, estimated - delivered_map.get(allocation.topic_id, 0))
    return remaining


def _blocked_marker(session: LectureSession) -> CalendarBlock:
    return CalendarBlock(
        date=ensure_utc(session.scheduled_start).date(),
        blocks_teaching=True,
        description="closed session",
    )


def build_calendar(
    db: Session, *, course: Course, range_start: date, range_end: date, view: str
) -> CalendarResponse:
    state = load_course_state(db, course)
    base = base_revision_for(db, course)
    sessions = revision_sessions(db, base) if base else []

    blocked = {
        item.date: (item.description or item.exception_type.value)
        for item in state.exceptions
        if item.blocks_teaching
    }

    by_day: dict[date, list[LectureSession]] = {}
    for session in sessions:
        day = ensure_utc(session.scheduled_start).date()
        if range_start <= day <= range_end:
            by_day.setdefault(day, []).append(session)

    days: list[CalendarDayRead] = []
    cursor = range_start
    while cursor <= range_end:
        entries = by_day.get(cursor, [])
        days.append(
            CalendarDayRead(
                date=cursor,
                weekday=cursor.weekday(),
                is_blocked=cursor in blocked,
                block_reason=blocked.get(cursor, ""),
                lectures=len(entries),
                minutes=sum(
                    allocation.allocated_duration_minutes
                    for session in entries
                    for allocation in session.allocations
                ),
                statuses=[session.status for session in entries],
            )
        )
        cursor += timedelta(days=1)

    return CalendarResponse(
        course_id=course.id,
        view=view,
        range_start=range_start,
        range_end=range_end,
        days=days,
    )


def calendar_entries(
    db: Session, *, course: Course, range_start: date, range_end: date
) -> list[dict[str, object]]:
    """Detailed entries for the day/week/semester calendar views."""

    base = base_revision_for(db, course)
    if base is None:
        return []
    entries: list[dict[str, object]] = []
    for session in revision_sessions(db, base):
        day = ensure_utc(session.scheduled_start).date()
        if not (range_start <= day <= range_end):
            continue
        entries.append(
            {
                "lecture_id": session.id,
                "sequence": session.sequence,
                "date": day.isoformat(),
                "scheduled_start": ensure_utc(session.scheduled_start),
                "scheduled_end": ensure_utc(session.scheduled_end),
                "status": session.status,
                "topics": [
                    allocation.topic.title
                    for allocation in session.allocations
                    if allocation.topic is not None
                ],
                "minutes": sum(
                    allocation.allocated_duration_minutes for allocation in session.allocations
                ),
                "is_published": session.is_published,
                "note": session.cancellation_reason or session.notes,
                "change_reason": (
                    "Redistributed after a missed lecture."
                    if session.carry_over_from_session_id
                    else ""
                ),
            }
        )
    entries.sort(key=lambda item: item["scheduled_start"])  # type: ignore[arg-type,return-value]
    return entries


def topic_progress_rows(
    db: Session, *, course: Course
) -> tuple[list[TopicProgressRead], list[UnitProgressRead]]:
    state = load_course_state(db, course)
    base = base_revision_for(db, course)
    sessions = revision_sessions(db, base) if base else []
    rows, units, _ = _topic_states(state, sessions, (base.explanation if base else {}) or {})
    return rows, units
