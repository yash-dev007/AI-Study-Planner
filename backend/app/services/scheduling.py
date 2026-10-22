"""Schedule application service.

Responsibilities, in order:

1. Load the persisted course state (topics, prerequisites, availability, calendar).
2. Determine what is already fixed history and what future lectures can be kept.
3. Build concrete lecture slots.
4. Run the deterministic planner (never the LLM) and validate the result.
5. Diff against the base revision and persist a new immutable revision that
   awaits the professor's approval.

A revision is a complete snapshot of the timetable (history plus the remaining
plan) so rollback restores a consistent view. Prior revisions are never mutated.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.timeutil import ensure_utc, now_utc
from app.domain.scheduling import (
    AvailabilityWindow,
    CalendarBlock,
    CalendarConfigurationError,
    OccupiedInterval,
    PlannerResult,
    PlannerSlot,
    PlannerTopic,
    ScheduleDiff,
    SlotPlan,
    build_slots,
    diff_results,
    plan_schedule,
    validate_plan,
)
from app.domain.scheduling.planner import PlannedAllocation, ValidationIssue
from app.models.entities import (
    AllocationStatus,
    ApprovalStatus,
    AvailabilityRule,
    CalendarException,
    Course,
    LectureSession,
    NotificationType,
    RevisionTrigger,
    ScheduleRevision,
    ScheduleRevisionChange,
    SessionStatus,
    SyllabusUnit,
    Topic,
    TopicAllocation,
    TopicDependency,
    User,
)
from app.schemas.schedule import (
    AllocationRead,
    CapacitySummary,
    DiffChangeRead,
    DiffSummary,
    LectureRead,
    RevisionSummary,
    ScheduleRead,
    UnplacedTopicRead,
    ValidationIssueRead,
)
from app.services.audit import notify, record_audit
from app.services.courses import course_settings

STRATEGY_PRESERVE = "preserve_future"
STRATEGY_REDISTRIBUTE = "redistribute_all"
STRATEGY_AUTO = "auto"
MAX_STORED_CHANGES = 300
DELIVERED_STATUSES = {AllocationStatus.COMPLETED, AllocationStatus.IN_PROGRESS}
CLOSED_SESSION_STATUSES = {
    SessionStatus.COMPLETED,
    SessionStatus.MISSED,
    SessionStatus.CANCELLED,
}


# --------------------------------------------------------------------------- #
# Course state
# --------------------------------------------------------------------------- #


@dataclass
class CourseState:
    course: Course
    units: list[SyllabusUnit]
    topics: dict[uuid.UUID, Topic]
    prerequisites: dict[uuid.UUID, frozenset[uuid.UUID]]
    dependents: dict[uuid.UUID, list[uuid.UUID]]
    rules: list[AvailabilityRule]
    exceptions: list[CalendarException]
    unit_titles: dict[uuid.UUID, str] = field(default_factory=dict)

    @property
    def titles(self) -> dict[uuid.UUID, str]:
        return {topic_id: topic.title for topic_id, topic in self.topics.items()}


def load_course_state(db: Session, course: Course) -> CourseState:
    units = list(
        db.execute(
            select(SyllabusUnit)
            .where(SyllabusUnit.course_id == course.id)
            .order_by(SyllabusUnit.sequence_number)
            .options(selectinload(SyllabusUnit.topics))
        )
        .scalars()
        .all()
    )
    topics = {topic.id: topic for unit in units for topic in unit.topics}
    unit_titles = {unit.id: unit.title for unit in units}
    prerequisites: dict[uuid.UUID, set[uuid.UUID]] = {topic_id: set() for topic_id in topics}
    dependents: dict[uuid.UUID, list[uuid.UUID]] = {topic_id: [] for topic_id in topics}
    if topics:
        rows = (
            db.execute(select(TopicDependency).where(TopicDependency.topic_id.in_(list(topics))))
            .scalars()
            .all()
        )
        for row in rows:
            if row.prerequisite_topic_id not in topics:
                continue
            prerequisites[row.topic_id].add(row.prerequisite_topic_id)
            dependents.setdefault(row.prerequisite_topic_id, []).append(row.topic_id)

    rules = list(
        db.execute(
            select(AvailabilityRule)
            .where(AvailabilityRule.course_id == course.id)
            .order_by(AvailabilityRule.weekday, AvailabilityRule.start_time)
        )
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
    return CourseState(
        course=course,
        units=units,
        topics=topics,
        prerequisites={key: frozenset(value) for key, value in prerequisites.items()},
        dependents=dependents,
        rules=rules,
        exceptions=exceptions,
        unit_titles=unit_titles,
    )


def active_course_state(state: CourseState) -> CourseState:
    """Remove archived units and topics before a new plan is built.

    Archived rows stay readable for history and progress, but they must never
    receive new allocations. Dependencies that point at archived topics are
    dropped so the plan only orders work that can still be taught.
    """

    units = [unit for unit in state.units if unit.archived_at is None]
    topics = {
        topic.id: topic
        for unit in units
        for topic in unit.topics
        if topic.archived_at is None
    }
    return CourseState(
        course=state.course,
        units=units,
        topics=topics,
        prerequisites={
            topic_id: frozenset(
                prerequisite
                for prerequisite in prerequisites
                if prerequisite in topics
            )
            for topic_id, prerequisites in state.prerequisites.items()
            if topic_id in topics
        },
        dependents={
            topic_id: [dependent for dependent in dependents if dependent in topics]
            for topic_id, dependents in state.dependents.items()
            if topic_id in topics
        },
        rules=state.rules,
        exceptions=state.exceptions,
        unit_titles={unit.id: unit.title for unit in units},
    )


def availability_windows(state: CourseState) -> list[AvailabilityWindow]:
    return [
        AvailabilityWindow(
            weekday=rule.weekday,
            start_time=rule.start_time,
            end_time=rule.end_time,
            room=rule.room,
            effective_from=rule.effective_from,
            effective_until=rule.effective_until,
            is_enabled=rule.is_enabled,
        )
        for rule in state.rules
    ]


def calendar_blocks(state: CourseState) -> list[CalendarBlock]:
    return [
        CalendarBlock(
            date=item.date,
            blocks_teaching=item.blocks_teaching,
            description=item.description or item.exception_type.value,
        )
        for item in state.exceptions
    ]


# --------------------------------------------------------------------------- #
# Revisions
# --------------------------------------------------------------------------- #


def revision_sessions(db: Session, revision: ScheduleRevision) -> list[LectureSession]:
    return list(
        db.execute(
            select(LectureSession)
            .where(LectureSession.revision_id == revision.id)
            .order_by(LectureSession.sequence, LectureSession.scheduled_start)
            .options(selectinload(LectureSession.allocations).selectinload(TopicAllocation.topic))
        )
        .scalars()
        .all()
    )


def latest_revision(
    db: Session, course: Course, *, approval_status: ApprovalStatus | None = None
) -> ScheduleRevision | None:
    query = select(ScheduleRevision).where(ScheduleRevision.course_id == course.id)
    if approval_status is not None:
        query = query.where(ScheduleRevision.approval_status == approval_status)
    return db.execute(
        query.order_by(ScheduleRevision.revision_number.desc()).limit(1)
    ).scalar_one_or_none()


def current_approved_revision(db: Session, course: Course) -> ScheduleRevision | None:
    return latest_revision(db, course, approval_status=ApprovalStatus.APPROVED)


def pending_revision(db: Session, course: Course) -> ScheduleRevision | None:
    return db.execute(
        select(ScheduleRevision)
        .where(
            ScheduleRevision.course_id == course.id,
            ScheduleRevision.approval_status.in_(
                [ApprovalStatus.DRAFT, ApprovalStatus.PENDING_APPROVAL]
            ),
        )
        .order_by(ScheduleRevision.revision_number.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_revision(db: Session, course: Course, revision_id: uuid.UUID) -> ScheduleRevision:
    revision = db.get(ScheduleRevision, revision_id)
    if revision is None or revision.course_id != course.id:
        raise NotFoundError("Schedule revision not found.", code="REVISION_NOT_FOUND")
    return revision


def base_revision_for(db: Session, course: Course) -> ScheduleRevision | None:
    """The revision a new recalculation should be based on.

    The approved revision wins. When nothing is approved yet, the newest draft is
    used so repeated generation from an unapproved timetable stays consistent.
    """

    approved = current_approved_revision(db, course)
    if approved is not None:
        return approved
    return latest_revision(db, course)


def _assert_base_is_current(
    db: Session, course: Course, base_revision_id: uuid.UUID | None, base: ScheduleRevision | None
) -> None:
    if base_revision_id is None or base is None:
        return
    if base_revision_id != base.id:
        raise ConflictError(
            "The schedule changed since you loaded it, so this recalculation is based on a "
            "stale revision. Reload the course and try again.",
            code="STALE_BASE_REVISION",
            details={
                "expected_base_revision_id": str(base_revision_id),
                "current_base_revision_id": str(base.id),
            },
        )


# --------------------------------------------------------------------------- #
# Delivered work
# --------------------------------------------------------------------------- #


def _allocation_delivered_minutes(allocation: TopicAllocation) -> int:
    if allocation.completion_status == AllocationStatus.SKIPPED:
        return 0
    if allocation.completion_status not in DELIVERED_STATUSES:
        return 0
    if allocation.actual_minutes is not None:
        return max(0, allocation.actual_minutes)
    return max(0, allocation.planned_minutes)


def delivered_minutes(sessions: list[LectureSession]) -> dict[uuid.UUID, int]:
    """Minutes actually taught per topic.

    Only completed lectures count. A missed or cancelled lecture contributes
    nothing, which is exactly why its topics are picked up again by the planner.
    """

    totals: dict[uuid.UUID, int] = {}
    for session in sessions:
        if session.status != SessionStatus.COMPLETED:
            continue
        for allocation in session.allocations:
            minutes = _allocation_delivered_minutes(allocation)
            if minutes:
                totals[allocation.topic_id] = totals.get(allocation.topic_id, 0) + minutes
    return totals


# --------------------------------------------------------------------------- #
# Preserved / carried sessions
# --------------------------------------------------------------------------- #


@dataclass
class BaseTimetable:
    history: list[LectureSession] = field(default_factory=list)
    preserved: list[LectureSession] = field(default_factory=list)
    occupied: list[OccupiedInterval] = field(default_factory=list)
    missed_topic_sources: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    unmarked_past: int = 0


def _collect_base_timetable(
    sessions: list[LectureSession], *, reference: datetime, keep_future: bool
) -> BaseTimetable:
    base = BaseTimetable()
    for session in sessions:
        start = ensure_utc(session.scheduled_start)
        end = ensure_utc(session.scheduled_end)
        if session.status == SessionStatus.COMPLETED:
            base.history.append(session)
            base.occupied.append(OccupiedInterval(start=start, end=end))
            continue
        if session.status in {SessionStatus.MISSED, SessionStatus.CANCELLED}:
            base.history.append(session)
            # The lecture did not happen, but the professor was unavailable, so
            # the same time must not be handed to another lecture.
            base.occupied.append(OccupiedInterval(start=start, end=end))
            for allocation in session.allocations:
                base.missed_topic_sources.setdefault(allocation.topic_id, session.id)
            continue
        if start < reference:
            base.history.append(session)
            base.occupied.append(OccupiedInterval(start=start, end=end))
            base.unmarked_past += 1
            continue
        if keep_future:
            base.preserved.append(session)
        else:
            for allocation in session.allocations:
                base.missed_topic_sources.setdefault(allocation.topic_id, session.id)

    if base.unmarked_past:
        base.warnings.append(
            f"{base.unmarked_past} past lecture(s) are still unmarked. Mark them completed or "
            "missed so the plan reflects what actually happened."
        )
    return base


@dataclass
class Preservation:
    preseeded: dict[int, list[PlannedAllocation]] = field(default_factory=dict)
    inherited: dict[datetime, LectureSession] = field(default_factory=dict)
    dropped: list[dict[str, object]] = field(default_factory=list)
    minutes: int = 0

    @property
    def session_count(self) -> int:
        return len(self.inherited)


def _preserve_future_sessions(
    *,
    state: CourseState,
    preserved: list[LectureSession],
    slots: list[PlannerSlot],
    committed_before: dict[uuid.UUID, int],
) -> Preservation:
    """Keep future lectures that are still valid, in place.

    A lecture is kept only when its exact slot still exists (it is not on a new
    holiday) and every topic it teaches is allowed to start at that point: all
    prerequisites must already be complete by then. Anything else is dropped and
    re-planned, which is reported to the professor.
    """

    slot_by_start = {slot.start: slot for slot in slots}
    by_start = {ensure_utc(session.scheduled_start): session for session in preserved}
    planned_committed = dict(committed_before)
    result = Preservation()

    for start in sorted(by_start):
        session = by_start[start]
        slot = slot_by_start.get(start)
        allocations: list[PlannedAllocation] = []

        if slot is None:
            result.dropped.append(
                {
                    "lecture_id": str(session.id),
                    "date": start.date().isoformat(),
                    "reason": "The lecture's time is no longer available (new block or holiday).",
                }
            )
            continue

        blocked = False
        for allocation in session.allocations:
            topic = state.topics.get(allocation.topic_id)
            if topic is None:
                blocked = True
                break
            available = max(
                0, topic.estimated_duration_minutes - planned_committed.get(topic.id, 0)
            )
            if available < allocation.planned_minutes:
                blocked = True
                break
            for prerequisite_id in state.prerequisites.get(topic.id, frozenset()):
                prerequisite = state.topics.get(prerequisite_id)
                if prerequisite is None:
                    continue
                if (
                    planned_committed.get(prerequisite_id, 0)
                    < prerequisite.estimated_duration_minutes
                ):
                    blocked = True
                    break
            if blocked:
                break

        if blocked or not session.allocations:
            result.dropped.append(
                {
                    "lecture_id": str(session.id),
                    "date": start.date().isoformat(),
                    "reason": (
                        "Its topics moved behind a prerequisite that is no longer complete in "
                        "time, so the lecture was re-planned."
                    ),
                }
            )
            continue

        for sequence, allocation in enumerate(session.allocations, start=1):
            allocations.append(
                PlannedAllocation(
                    topic_id=allocation.topic_id,
                    minutes=allocation.planned_minutes,
                    sequence_in_session=allocation.sequence_in_session or sequence,
                )
            )
            planned_committed[allocation.topic_id] = (
                planned_committed.get(allocation.topic_id, 0)
                + allocation.planned_minutes
            )

        result.preseeded[slot.index] = allocations
        result.inherited[start] = session
        result.minutes += sum(item.minutes for item in allocations)

    return result


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #


@dataclass
class PlanRun:
    result: PlannerResult
    issues: list[ValidationIssue]
    base: BaseTimetable
    preservation: Preservation
    slots: list[PlannerSlot]
    strategy: str
    warnings: list[str]
    delivered: dict[uuid.UUID, int]
    titles: dict[uuid.UUID, str] = field(default_factory=dict)


def _validation_topics(
    state: CourseState, *, delivered: dict[uuid.UUID, int], committed: dict[uuid.UUID, int]
) -> list[PlannerTopic]:
    topics: list[PlannerTopic] = []
    for topic in state.topics.values():
        total_committed = delivered.get(topic.id, 0) + committed.get(topic.id, 0)
        topics.append(
            PlannerTopic(
                id=topic.id,
                key=str(topic.id),
                title=topic.title,
                unit_sequence=_unit_sequence(state, topic),
                sequence=topic.sequence_number,
                estimated_minutes=topic.estimated_duration_minutes,
                completed_minutes=total_committed,
                priority=topic.priority,
                is_splittable=topic.is_splittable,
                prerequisite_ids=state.prerequisites.get(topic.id, frozenset()),
            )
        )
    return topics


def _unit_sequence(state: CourseState, topic: Topic) -> int:
    for unit in state.units:
        if unit.id == topic.unit_id:
            return unit.sequence_number
    return 1


def run_plan(
    *,
    state: CourseState,
    base_sessions: list[LectureSession],
    reference: datetime,
    strategy: str,
    include_whole_semester: bool = False,
    extra_blocked_dates: list[datetime] | None = None,
) -> PlanRun:
    """Build slots, preserve what can be preserved, and run the planner."""

    settings = course_settings(state.course)
    course = state.course

    if not state.topics:
        raise ValidationFailedError(
            "This course has no topics yet. Upload and confirm a syllabus before scheduling.",
            code="NO_TOPICS",
        )

    if strategy == STRATEGY_AUTO:
        strategy = STRATEGY_PRESERVE if settings.preserve_future_lectures else STRATEGY_REDISTRIBUTE

    keep_future = strategy == STRATEGY_PRESERVE
    base = _collect_base_timetable(base_sessions, reference=reference, keep_future=keep_future)
    delivered = delivered_minutes(base_sessions)

    blocks = calendar_blocks(state)
    for blocked_at in extra_blocked_dates or []:
        blocks.append(
            CalendarBlock(
                date=ensure_utc(blocked_at).date(),
                blocks_teaching=True,
                description="reserved",
            )
        )

    not_before = None if include_whole_semester else reference
    try:
        slots = build_slots(
            start_date=course.start_date,
            end_date=course.end_date,
            timezone=course.timezone,
            lecture_duration_minutes=course.lecture_duration_minutes,
            windows=availability_windows(state),
            blocks=blocks,
            occupied=base.occupied,
            not_before=not_before,
        )
    except CalendarConfigurationError as exc:
        raise ValidationFailedError(str(exc), code="CALENDAR_CONFIGURATION") from exc

    warnings = list(base.warnings)
    preservation = Preservation()
    if keep_future and base.preserved:
        preservation = _preserve_future_sessions(
            state=state,
            preserved=base.preserved,
            slots=slots,
            committed_before=delivered,
        )
        if preservation.dropped:
            warnings.append(
                f"{len(preservation.dropped)} future lecture(s) could not be kept in place and "
                "were re-planned. See the revision explanation for details."
            )

    planning_topics = []
    for topic in state.topics.values():
        planning_topics.append(
            PlannerTopic(
                id=topic.id,
                key=str(topic.id),
                title=topic.title,
                unit_sequence=_unit_sequence(state, topic),
                sequence=topic.sequence_number,
                estimated_minutes=topic.estimated_duration_minutes,
                completed_minutes=delivered.get(topic.id, 0),
                priority=topic.priority,
                is_splittable=topic.is_splittable,
                prerequisite_ids=state.prerequisites.get(topic.id, frozenset()),
            )
        )

    result = plan_schedule(
        planning_topics,
        slots,
        preseeded=preservation.preseeded,
        min_allocation_minutes=settings.min_allocation_minutes,
        max_topics_per_slot=None if settings.combine_short_topics else 1,
    )
    warnings.extend(result.warnings)

    committed = {topic_id: 0 for topic_id in state.topics}
    for allocations in preservation.preseeded.values():
        for allocation in allocations:
            committed[allocation.topic_id] = (
                committed.get(allocation.topic_id, 0) + allocation.minutes
            )

    issues = validate_plan(
        _validation_topics(state, delivered=delivered, committed=committed), result
    )
    for issue in issues:
        if issue.severity == "error":
            warnings.append(f"Validation error ({issue.code}): {issue.message}")
        elif issue.code != "unplaced_topic":
            warnings.append(issue.message)

    return PlanRun(
        result=result,
        issues=issues,
        base=base,
        preservation=preservation,
        slots=slots,
        strategy=strategy,
        warnings=warnings,
        delivered=delivered,
        titles=state.titles,
    )


def _result_from_sessions(sessions: list[LectureSession]) -> PlannerResult:
    """Reconstruct a planner result from persisted sessions, for diffing."""

    slot_plans: list[SlotPlan] = []
    first: dict[uuid.UUID, datetime] = {}
    last: dict[uuid.UUID, datetime] = {}
    for index, session in enumerate(
        sorted(sessions, key=lambda item: ensure_utc(item.scheduled_start)), start=1
    ):
        slot = PlannerSlot(
            index=index,
            start=ensure_utc(session.scheduled_start),
            end=ensure_utc(session.scheduled_end),
            capacity_minutes=max(
                1,
                int(
                    (
                        ensure_utc(session.scheduled_end) - ensure_utc(session.scheduled_start)
                    ).total_seconds()
                    // 60
                ),
            ),
        )
        plan = SlotPlan(slot=slot)
        for allocation in sorted(session.allocations, key=lambda item: item.sequence_in_session):
            plan.allocations.append(
                PlannedAllocation(
                    topic_id=allocation.topic_id,
                    minutes=allocation.planned_minutes,
                    sequence_in_session=allocation.sequence_in_session,
                )
            )
            first.setdefault(allocation.topic_id, slot.start)
            last[allocation.topic_id] = slot.end
        slot_plans.append(plan)
    return PlannerResult(
        slots=slot_plans,
        unplaced_topics=[],
        warnings=[],
        metrics={},
        first_allocation=first,
        last_allocation=last,
    )


def _diff_summary(diff: ScheduleDiff) -> DiffSummary:
    return DiffSummary(
        summary=diff.summary,
        added_count=len(diff.added),
        removed_count=len(diff.removed),
        moved_count=len(diff.moved),
        changes=[
            DiffChangeRead(
                change_type=change.change_type,
                topic_id=change.topic_id,
                topic_title=change.topic_title,
                previous_date=change.previous_date,
                new_date=change.new_date,
                previous_minutes=change.previous_minutes,
                new_minutes=change.new_minutes,
                change_reason=change.change_reason,
            )
            for change in diff.changes[:MAX_STORED_CHANGES]
        ],
    )


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def _next_revision_number(db: Session, course: Course) -> int:
    current = db.execute(
        select(func.max(ScheduleRevision.revision_number)).where(
            ScheduleRevision.course_id == course.id
        )
    ).scalar_one_or_none()
    return int(current or 0) + 1


def _persist_revision(
    db: Session,
    *,
    course: Course,
    user: User,
    run: PlanRun,
    trigger: RevisionTrigger,
    reason: str,
    base_revision: ScheduleRevision | None,
    trigger_lecture_id: uuid.UUID | None,
    previous_planned_sessions: list[LectureSession] | None = None,
) -> ScheduleRevision:
    # A new proposal replaces any proposal that is still waiting for review, so
    # there is never more than one pending draft per course.
    for stale in (
        db.execute(
            select(ScheduleRevision).where(
                ScheduleRevision.course_id == course.id,
                ScheduleRevision.approval_status.in_(
                    [ApprovalStatus.DRAFT, ApprovalStatus.PENDING_APPROVAL]
                ),
            )
        )
        .scalars()
        .all()
    ):
        stale.approval_status = ApprovalStatus.SUPERSEDED
        if base_revision is not None and stale.id == base_revision.id:
            base_revision = None
    db.flush()

    revision = ScheduleRevision(
        course_id=course.id,
        revision_number=_next_revision_number(db, course),
        trigger_type=trigger,
        trigger_lecture_id=trigger_lecture_id,
        approval_status=ApprovalStatus.PENDING_APPROVAL,
        created_by=user.id,
        base_revision_id=base_revision.id if base_revision else None,
    )
    db.add(revision)
    db.flush()

    # History is carried forward verbatim and the newly planned slots are added
    # on top. Lecture numbers are assigned before anything is flushed because
    # (revision, sequence) is unique and rows are inserted as they are created.
    entries: list[tuple[datetime, str, LectureSession | SlotPlan]] = [
        (ensure_utc(session.scheduled_start), "history", session) for session in run.base.history
    ]
    entries.extend(
        (ensure_utc(slot_plan.slot.start), "planned", slot_plan)
        for slot_plan in run.result.slots
        if slot_plan.allocations
    )
    entries.sort(key=lambda item: item[0])

    sessions: list[LectureSession] = []

    for sequence, (_start, kind, payload) in enumerate(entries, start=1):
        if kind == "history":
            source_session = cast(LectureSession, payload)
            clone = LectureSession(
                course_id=course.id,
                revision_id=revision.id,
                sequence=sequence,
                scheduled_start=ensure_utc(source_session.scheduled_start),
                scheduled_end=ensure_utc(source_session.scheduled_end),
                status=source_session.status,
                actual_start=(
                    ensure_utc(source_session.actual_start) if source_session.actual_start else None
                ),
                actual_end=(
                    ensure_utc(source_session.actual_end) if source_session.actual_end else None
                ),
                cancellation_reason=source_session.cancellation_reason,
                notes=source_session.notes,
                is_published=False,
                carry_over_from_session_id=source_session.carry_over_from_session_id,
            )
            db.add(clone)
            db.flush()
            # A lecture that was missed or cancelled never delivered its work, so
            # its allocations are recorded as skipped and the topics are taught in
            # a later lecture instead.
            closed = source_session.status in {SessionStatus.MISSED, SessionStatus.CANCELLED}
            for allocation in source_session.allocations:
                allocation_status = allocation.completion_status
                if closed and allocation_status == AllocationStatus.PLANNED:
                    allocation_status = AllocationStatus.SKIPPED
                db.add(
                    TopicAllocation(
                        lecture_session_id=clone.id,
                        topic_id=allocation.topic_id,
                        planned_minutes=allocation.planned_minutes,
                        completion_status=allocation_status,
                        actual_minutes=allocation.actual_minutes,
                        sequence_in_session=allocation.sequence_in_session,
                    )
                )
            sessions.append(clone)
            continue

        slot_plan = cast(SlotPlan, payload)
        inherited = run.preservation.inherited.get(slot_plan.slot.start)
        carry_over = None
        session = LectureSession(
            course_id=course.id,
            revision_id=revision.id,
            sequence=sequence,
            scheduled_start=ensure_utc(slot_plan.slot.start),
            scheduled_end=ensure_utc(slot_plan.slot.end),
            status=SessionStatus.SCHEDULED,
            notes=inherited.notes if inherited is not None else "",
            is_published=False,
            carry_over_from_session_id=inherited.carry_over_from_session_id if inherited else None,
        )
        for planned in slot_plan.allocations:
            source = run.base.missed_topic_sources.get(planned.topic_id)
            if source is not None and carry_over is None:
                carry_over = source
        if carry_over is not None and session.carry_over_from_session_id is None:
            session.carry_over_from_session_id = carry_over
        db.add(session)
        db.flush()
        for planned in slot_plan.allocations:
            db.add(
                TopicAllocation(
                    lecture_session_id=session.id,
                    topic_id=planned.topic_id,
                    planned_minutes=planned.minutes,
                    completion_status=AllocationStatus.PLANNED,
                    sequence_in_session=planned.sequence_in_session,
                )
            )
        sessions.append(session)

    db.flush()

    previous_result = (
        _result_from_sessions(previous_planned_sessions or [])
        if previous_planned_sessions is not None
        else None
    )
    diff = diff_results(
        previous_result,
        run.result,
        titles=run.titles,
        reason=reason or "Schedule recalculated.",
    )

    unplaced = [
        UnplacedTopicRead(
            topic_id=item.topic_id,
            title=item.title,
            remaining_minutes=item.remaining_minutes,
            reason=item.reason,
        )
        for item in run.result.unplaced_topics
    ]
    issues = [
        ValidationIssueRead(
            code=issue.code,
            message=issue.message,
            severity=issue.severity,
            topic_id=issue.topic_id,
            slot_index=issue.slot_index,
        )
        for issue in run.issues
    ]

    explanation = {
        "trigger": trigger.value,
        "reason": reason or "",
        "strategy": run.strategy,
        "preserved_lectures": run.preservation.session_count,
        "preserved_minutes": run.preservation.minutes,
        "dropped_preserved_lectures": run.preservation.dropped,
        "unplaced_topics": [item.model_dump(mode="json") for item in unplaced],
        "validation_issues": [item.model_dump(mode="json") for item in issues],
        "diff": _diff_summary(diff).model_dump(mode="json"),
        "remaining_minutes_by_topic": run.result.metrics.get("remaining_minutes", 0),
    }

    revision.change_summary = _change_summary(trigger, diff, reason)
    revision.explanation = explanation
    revision.warnings = _result_warnings(run)
    revision.metrics = dict(run.result.metrics)

    for change in diff.changes[:MAX_STORED_CHANGES]:
        db.add(
            ScheduleRevisionChange(
                revision_id=revision.id,
                lecture_session_id=None,
                topic_id=change.topic_id,
                change_type=change.change_type,
                previous_date=change.previous_date.date() if change.previous_date else None,
                new_date=change.new_date.date() if change.new_date else None,
                previous_minutes=change.previous_minutes,
                new_minutes=change.new_minutes,
                change_reason=change.change_reason,
            )
        )
    db.flush()
    return revision


def _result_warnings(run: PlanRun) -> list[str]:
    """Warnings the professor must see, in the order they matter."""

    warnings = list(run.warnings)
    redistributed = len(run.base.missed_topic_sources)
    if redistributed:
        warnings.append(
            f"{redistributed} topic(s) from missed or cancelled lectures were redistributed "
            "across the remaining lectures."
        )
    if run.preservation.dropped:
        warnings.append(
            f"{len(run.preservation.dropped)} previously planned lecture(s) could not be kept "
            "because their topics no longer fit that date. They were re-planned."
        )
    if run.result.unplaced_topics:
        warnings.append(
            f"{len(run.result.unplaced_topics)} topic(s) could not be placed in the remaining "
            "calendar. Add sessions or extend availability."
        )
    return warnings


def _change_summary(trigger: RevisionTrigger, diff: ScheduleDiff, reason: str) -> str:
    labels = {
        RevisionTrigger.GENERATED: "Initial schedule generated",
        RevisionTrigger.LECTURE_MISSED: "Schedule recalculated after a missed lecture",
        RevisionTrigger.LECTURE_COMPLETED: "Schedule recalculated after a completed lecture",
        RevisionTrigger.MANUAL_EDIT: "Schedule recalculated after a manual change",
        RevisionTrigger.COURSE_CONFIG_CHANGED: "Schedule recalculated after a configuration change",
        RevisionTrigger.ROLLBACK: "Schedule restored from an earlier revision",
    }
    label = labels.get(trigger, "Schedule recalculated")
    parts = [label]
    if reason:
        parts.append(reason)
    if diff.moved:
        parts.append(f"{len(diff.moved)} lecture(s) moved")
    if diff.added:
        parts.append(f"{len(diff.added)} allocation(s) added")
    if diff.removed:
        parts.append(f"{len(diff.removed)} allocation(s) removed")
    return ". ".join(parts) + "."


# --------------------------------------------------------------------------- #
# Public operations
# --------------------------------------------------------------------------- #


def generate_schedule(
    db: Session,
    *,
    course: Course,
    user: User,
    trigger: RevisionTrigger = RevisionTrigger.GENERATED,
    strategy: str = STRATEGY_AUTO,
    reason: str = "",
    base_revision_id: uuid.UUID | None = None,
    trigger_lecture_id: uuid.UUID | None = None,
    include_whole_semester: bool | None = None,
    reference: datetime | None = None,
) -> ScheduleRevision:
    """Generate or recalculate a schedule and store it as a pending revision."""

    state = active_course_state(load_course_state(db, course))
    base_revision = base_revision_for(db, course)
    _assert_base_is_current(db, course, base_revision_id, base_revision)

    base_sessions = revision_sessions(db, base_revision) if base_revision else []
    whole_semester = (
        include_whole_semester if include_whole_semester is not None else base_revision is None
    )
    moment = reference or now_utc()
    previous_planned_sessions = [
        session
        for session in base_sessions
        if session.status == SessionStatus.SCHEDULED
        and ensure_utc(session.scheduled_start) >= moment
    ]

    run = run_plan(
        state=state,
        base_sessions=base_sessions,
        reference=moment,
        strategy=strategy,
        include_whole_semester=whole_semester,
    )

    revision = _persist_revision(
        db,
        course=course,
        user=user,
        run=run,
        trigger=trigger,
        reason=reason,
        base_revision=base_revision,
        trigger_lecture_id=trigger_lecture_id,
        previous_planned_sessions=previous_planned_sessions,
    )
    course.version += 1
    db.flush()

    settings = course_settings(course)
    published_automatically = False
    if settings.auto_publish_reschedules:
        approve_revision(
            db,
            course=course,
            revision=revision,
            user=user,
            note="Published automatically by the course setting.",
            publish=True,
        )
        published_automatically = True

    diff_summary = (revision.explanation or {}).get("diff", {})
    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="schedule.generated",
        entity_type="schedule_revision",
        entity_id=str(revision.id),
        detail={
            "trigger": trigger.value,
            "strategy": run.strategy,
            "revision_number": revision.revision_number,
            "moved": int(diff_summary.get("moved_count", 0)),
            "removed": int(diff_summary.get("removed_count", 0)),
            "unplaced": len((revision.explanation or {}).get("unplaced_topics", [])),
            "published_automatically": published_automatically,
        },
    )
    if not published_automatically:
        notify_pending(db, course=course, revision=revision, user=user)
    return revision


def notify_pending(db: Session, *, course: Course, revision: ScheduleRevision, user: User) -> None:
    unplaced = len((revision.explanation or {}).get("unplaced_topics", []))
    message = (
        f"Revision {revision.revision_number} for '{course.name}' is ready for review. "
        f"{revision.change_summary}"
    )
    if unplaced:
        message += f" {unplaced} topic(s) could not be placed."
    notify(
        db,
        user_id=user.id,
        course_id=course.id,
        revision_id=revision.id,
        event_type=(
            NotificationType.CAPACITY_WARNING if unplaced else NotificationType.SCHEDULE_PENDING
        ),
        message=message,
    )


def approve_revision(
    db: Session,
    *,
    course: Course,
    revision: ScheduleRevision,
    user: User,
    note: str = "",
    publish: bool = True,
) -> ScheduleRevision:
    """Approve (and optionally publish) a revision.

    A revision can only be approved while it is still based on the currently
    approved schedule. If another revision was approved in the meantime the
    approval is refused, so two professors (or two browser tabs) cannot silently
    overwrite each other.
    """

    if revision.approval_status == ApprovalStatus.APPROVED:
        return revision
    if revision.approval_status == ApprovalStatus.SUPERSEDED:
        raise ConflictError(
            "This revision was superseded by a newer schedule.", code="REVISION_SUPERSEDED"
        )
    if revision.approval_status == ApprovalStatus.REJECTED:
        raise ConflictError(
            "This revision was rejected. Recalculate the schedule to create a new proposal.",
            code="REVISION_REJECTED",
        )

    current = current_approved_revision(db, course)
    if (
        current is not None
        and revision.base_revision_id is not None
        and revision.base_revision_id != current.id
    ):
        raise ConflictError(
            "A newer schedule was approved after this revision was created. "
            "Reload the course and recalculate before approving.",
            code="STALE_REVISION_APPROVAL",
            details={
                "base_revision_id": str(revision.base_revision_id),
                "current_revision_id": str(current.id),
            },
        )

    revision.approval_status = ApprovalStatus.APPROVED
    revision.approved_by = user.id
    revision.approved_at = now_utc()
    if note:
        revision.explanation = {
            **(revision.explanation or {}),
            "approval_note": note,
        }
    if publish:
        revision.published_at = now_utc()

    if current is not None and current.id != revision.id:
        current.approval_status = ApprovalStatus.SUPERSEDED

    for session in revision_sessions(db, revision):
        session.is_published = publish

    course.version += 1
    db.flush()

    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="schedule.approved",
        entity_type="schedule_revision",
        entity_id=str(revision.id),
        detail={
            "revision_number": revision.revision_number,
            "published": publish,
            "superseded_revision_id": str(current.id) if current else None,
        },
    )
    if publish:
        notify(
            db,
            user_id=user.id,
            course_id=course.id,
            revision_id=revision.id,
            event_type=NotificationType.SCHEDULE_APPROVED,
            message=(
                f"Revision {revision.revision_number} for '{course.name}' was approved and "
                f"published. {revision.change_summary}"
            ),
        )
    return revision


def reject_revision(
    db: Session, *, course: Course, revision: ScheduleRevision, user: User, reason: str = ""
) -> ScheduleRevision:
    if revision.approval_status == ApprovalStatus.APPROVED:
        raise ConflictError(
            "This revision is already approved. Recalculate the schedule instead.",
            code="REVISION_ALREADY_APPROVED",
        )
    revision.approval_status = ApprovalStatus.REJECTED
    if reason:
        revision.explanation = {**(revision.explanation or {}), "rejection_reason": reason}
    db.flush()
    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="schedule.rejected",
        entity_type="schedule_revision",
        entity_id=str(revision.id),
        detail={"revision_number": revision.revision_number, "reason": reason},
    )
    return revision


def rollback_to_revision(
    db: Session, *, course: Course, revision: ScheduleRevision, user: User, reason: str = ""
) -> ScheduleRevision:
    """Restore a previous revision by creating a new, approved revision.

    History is never rewritten: the restored timetable becomes the newest
    revision, and both the rolled-back and the restored revisions stay readable.
    """

    source_sessions = revision_sessions(db, revision)
    if not source_sessions:
        raise ConflictError("That revision has no lectures to restore.", code="REVISION_EMPTY")

    new_revision = ScheduleRevision(
        course_id=course.id,
        revision_number=_next_revision_number(db, course),
        trigger_type=RevisionTrigger.ROLLBACK,
        approval_status=ApprovalStatus.PENDING_APPROVAL,
        change_summary="",
        created_by=user.id,
        base_revision_id=revision.id,
    )
    db.add(new_revision)
    db.flush()

    for session in source_sessions:
        clone = LectureSession(
            course_id=course.id,
            revision_id=new_revision.id,
            sequence=session.sequence,
            scheduled_start=ensure_utc(session.scheduled_start),
            scheduled_end=ensure_utc(session.scheduled_end),
            status=session.status,
            actual_start=ensure_utc(session.actual_start) if session.actual_start else None,
            actual_end=ensure_utc(session.actual_end) if session.actual_end else None,
            cancellation_reason=session.cancellation_reason,
            notes=session.notes,
            is_published=False,
            carry_over_from_session_id=session.carry_over_from_session_id,
        )
        db.add(clone)
        db.flush()
        for allocation in session.allocations:
            db.add(
                TopicAllocation(
                    lecture_session_id=clone.id,
                    topic_id=allocation.topic_id,
                    planned_minutes=allocation.planned_minutes,
                    completion_status=allocation.completion_status,
                    actual_minutes=allocation.actual_minutes,
                    sequence_in_session=allocation.sequence_in_session,
                )
            )
    db.flush()

    current = current_approved_revision(db, course)
    new_revision.change_summary = _change_summary(RevisionTrigger.ROLLBACK, ScheduleDiff(), reason)
    new_revision.explanation = {
        "trigger": RevisionTrigger.ROLLBACK.value,
        "reason": reason,
        "restored_from_revision": revision.revision_number,
        "restored_session_count": len(source_sessions),
        "diff": _diff_summary(ScheduleDiff()).model_dump(mode="json"),
        "unplaced_topics": [],
        "validation_issues": [],
    }
    new_revision.warnings = [
        f"The timetable was restored from revision {revision.revision_number}."
    ]
    new_revision.metrics = dict(revision.metrics or {})
    new_revision.approval_status = ApprovalStatus.APPROVED
    new_revision.approved_by = user.id
    new_revision.approved_at = now_utc()
    new_revision.published_at = now_utc()
    if current is not None and current.id != new_revision.id:
        current.approval_status = ApprovalStatus.SUPERSEDED
    for session in revision_sessions(db, new_revision):
        session.is_published = True
    db.flush()

    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="schedule.rolled_back",
        entity_type="schedule_revision",
        entity_id=str(new_revision.id),
        detail={
            "restored_from_revision": revision.revision_number,
            "new_revision_number": new_revision.revision_number,
        },
    )
    notify(
        db,
        user_id=user.id,
        course_id=course.id,
        revision_id=new_revision.id,
        event_type=NotificationType.SCHEDULE_APPROVED,
        message=(
            f"'{course.name}' was restored to revision {revision.revision_number} and the "
            f"result was published as revision {new_revision.revision_number}."
        ),
    )
    return new_revision


# --------------------------------------------------------------------------- #
# Read models
# --------------------------------------------------------------------------- #


def build_schedule_read(
    db: Session, *, course: Course, revision: ScheduleRevision | None
) -> ScheduleRead:
    if revision is None:
        return ScheduleRead(
            revision=None,
            lectures=[],
            requires_approval=False,
        )

    sessions = revision_sessions(db, revision)
    explanation = revision.explanation or {}
    metrics = revision.metrics or {}

    lectures: list[LectureRead] = []
    for session in sessions:
        allocations = [
            AllocationRead(
                id=allocation.id,
                topic_id=allocation.topic_id,
                topic_title=allocation.topic.title if allocation.topic else "",
                unit_title="",
                planned_minutes=allocation.planned_minutes,
                sequence_in_session=allocation.sequence_in_session,
                completion_status=allocation.completion_status,
                actual_minutes=allocation.actual_minutes,
            )
            for allocation in sorted(session.allocations, key=lambda item: item.sequence_in_session)
        ]
        lectures.append(
            LectureRead(
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
                allocations=allocations,
            )
        )

    expected_completion = metrics.get("expected_completion")
    capacity = CapacitySummary(
        sessions_available=int(metrics.get("sessions_available", 0) or 0),
        sessions_used=int(metrics.get("sessions_used", 0) or 0),
        capacity_minutes=int(metrics.get("capacity_minutes", 0) or 0),
        scheduled_minutes=int(metrics.get("scheduled_minutes", 0) or 0),
        completed_minutes=int(metrics.get("completed_minutes", 0) or 0),
        remaining_minutes=int(metrics.get("remaining_minutes", 0) or 0),
        utilization_percent=float(metrics.get("utilization_percent", 0.0) or 0.0),
        coverage_percent=float(metrics.get("coverage_percent", 0.0) or 0.0),
        projected_coverage_percent=float(metrics.get("projected_coverage_percent", 0.0) or 0.0),
        additional_sessions_required=int(metrics.get("additional_sessions_required", 0) or 0),
        expected_completion=(
            datetime.fromisoformat(str(expected_completion)) if expected_completion else None
        ),
        is_feasible=bool(metrics.get("is_feasible", True)),
    )

    return ScheduleRead(
        revision=revision_summary(revision),
        lectures=lectures,
        unplaced_topics=[
            UnplacedTopicRead.model_validate(item)
            for item in explanation.get("unplaced_topics", [])
        ],
        validation_issues=[
            ValidationIssueRead.model_validate(item)
            for item in explanation.get("validation_issues", [])
        ],
        diff=(DiffSummary.model_validate(explanation["diff"]) if explanation.get("diff") else None),
        capacity=capacity,
        requires_approval=revision.approval_status
        in {ApprovalStatus.DRAFT, ApprovalStatus.PENDING_APPROVAL},
    )


def revision_summary(revision: ScheduleRevision) -> RevisionSummary:
    return RevisionSummary.model_validate(
        {
            "id": revision.id,
            "course_id": revision.course_id,
            "revision_number": revision.revision_number,
            "trigger_type": revision.trigger_type,
            "approval_status": revision.approval_status,
            "change_summary": revision.change_summary,
            "explanation": revision.explanation or {},
            "warnings": revision.warnings or [],
            "metrics": revision.metrics or {},
            "created_by": revision.created_by,
            "approved_by": revision.approved_by,
            "approved_at": revision.approved_at,
            "published_at": revision.published_at,
            "base_revision_id": revision.base_revision_id,
            "created_at": revision.created_at,
        }
    )
