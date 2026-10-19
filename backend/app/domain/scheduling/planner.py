"""Deterministic, dependency-aware lecture scheduling.

This module is intentionally free of FastAPI, SQLAlchemy, and LLM imports so the
core scheduling behaviour can be unit-tested without a database or network.

Algorithm summary
-----------------
1. Build a dependency graph over topics with remaining effort. Completed
   prerequisites are treated as satisfied; cycles are rejected.
2. Walk lecture slots chronologically. At each slot, take the highest-priority
   ready topic:
   - if it fits, allocate it fully (this naturally combines short topics);
   - if it is splittable and does not fit, fill the slot and continue it later;
   - if it is not splittable, defer it and try another ready topic that fits.
3. If effort remains after all slots, the proposal is infeasible. The result
   still exposes the partial plan and a precise shortfall, but it is never
   presented as a valid complete timetable.

The hard constraints are: semester slots only, slot capacity, prerequisite
ordering, and no duplicate allocation within a slot. Soft preferences are
handled by ordering and by deferring non-splittable topics rather than
fragmenting them.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

MIN_ALLOCATION_MINUTES = 5


class SchedulingError(Exception):
    """Base class for domain scheduling failures."""


class DependencyCycleError(SchedulingError):
    def __init__(self, topic_ids: list[UUID]) -> None:
        self.topic_ids = topic_ids
        super().__init__(
            "The topic dependency graph contains a cycle, so no valid teaching order exists."
        )


class InvalidTopicError(SchedulingError):
    pass


@dataclass(frozen=True, slots=True)
class PlannerTopic:
    id: UUID
    key: str
    title: str
    unit_sequence: int
    sequence: int
    estimated_minutes: int
    completed_minutes: int = 0
    priority: int = 3
    is_splittable: bool = True
    prerequisite_ids: frozenset[UUID] = field(default_factory=frozenset)

    @property
    def remaining_minutes(self) -> int:
        return max(0, self.estimated_minutes - max(0, self.completed_minutes))

    @property
    def sort_key(self) -> tuple[int, int, int, str]:
        return (self.priority, self.unit_sequence, self.sequence, str(self.id))


@dataclass(frozen=True, slots=True)
class PlannerSlot:
    index: int
    start: datetime
    end: datetime
    capacity_minutes: int

    @property
    def date(self) -> date:
        return self.start.date()


@dataclass(frozen=True, slots=True)
class PlannedAllocation:
    topic_id: UUID
    minutes: int
    sequence_in_session: int


@dataclass
class SlotPlan:
    slot: PlannerSlot
    allocations: list[PlannedAllocation] = field(default_factory=list)

    @property
    def allocated_minutes(self) -> int:
        return sum(allocation.minutes for allocation in self.allocations)

    @property
    def remaining_minutes(self) -> int:
        return self.slot.capacity_minutes - self.allocated_minutes


@dataclass(frozen=True, slots=True)
class UnplacedTopic:
    topic_id: UUID
    title: str
    remaining_minutes: int
    reason: str


@dataclass
class PlannerResult:
    slots: list[SlotPlan]
    unplaced_topics: list[UnplacedTopic]
    warnings: list[str]
    metrics: dict[str, object]
    first_allocation: dict[UUID, datetime]
    last_allocation: dict[UUID, datetime]

    @property
    def is_feasible(self) -> bool:
        return not self.unplaced_topics

    @property
    def scheduled_minutes(self) -> int:
        return sum(plan.allocated_minutes for plan in self.slots)


def _validate_topics(topics: Iterable[PlannerTopic]) -> dict[UUID, PlannerTopic]:
    by_id: dict[UUID, PlannerTopic] = {}
    for topic in topics:
        if topic.estimated_minutes <= 0:
            raise InvalidTopicError(f"Topic '{topic.title}' must have a positive duration.")
        if topic.completed_minutes < 0:
            raise InvalidTopicError(f"Topic '{topic.title}' has negative completed minutes.")
        if topic.id in by_id:
            raise InvalidTopicError(f"Duplicate topic id: {topic.id}")
        by_id[topic.id] = topic
    for topic in by_id.values():
        unknown = topic.prerequisite_ids - by_id.keys()
        if unknown:
            raise InvalidTopicError(
                f"Topic '{topic.title}' references unknown prerequisites: "
                f"{sorted(map(str, unknown))}"
            )
        if topic.id in topic.prerequisite_ids:
            raise InvalidTopicError(f"Topic '{topic.title}' cannot depend on itself.")
    return by_id


def topological_order(topics: dict[UUID, PlannerTopic]) -> list[PlannerTopic]:
    """Kahn's algorithm with a deterministic priority queue.

    Prerequisites outside ``topics`` are treated as already satisfied (for
    example, a completed topic excluded from the remaining-work graph).
    """

    known = set(topics)
    outstanding = {topic.id: len(topic.prerequisite_ids & known) for topic in topics.values()}
    dependents: dict[UUID, list[UUID]] = {topic_id: [] for topic_id in topics}
    for topic in topics.values():
        for prerequisite_id in topic.prerequisite_ids & known:
            dependents[prerequisite_id].append(topic.id)

    heap: list[tuple[int, int, int, str, UUID]] = []
    for topic in topics.values():
        if outstanding[topic.id] == 0:
            heapq.heappush(heap, (*topic.sort_key, topic.id))

    ordered: list[PlannerTopic] = []
    while heap:
        _, _, _, _, topic_id = heapq.heappop(heap)
        topic = topics[topic_id]
        ordered.append(topic)
        for dependent_id in dependents[topic_id]:
            outstanding[dependent_id] -= 1
            if outstanding[dependent_id] == 0:
                dependent = topics[dependent_id]
                heapq.heappush(heap, (*dependent.sort_key, dependent.id))

    if len(ordered) != len(topics):
        cycle_ids = [topic_id for topic_id, count in outstanding.items() if count > 0]
        raise DependencyCycleError(cycle_ids)
    return ordered


def plan_schedule(
    topics: Iterable[PlannerTopic],
    slots: Iterable[PlannerSlot],
    *,
    reserved_minutes: dict[int, int] | None = None,
    earliest_slot: dict[UUID, int] | None = None,
    preseeded: dict[int, list[PlannedAllocation]] | None = None,
    min_allocation_minutes: int = MIN_ALLOCATION_MINUTES,
    max_topics_per_slot: int | None = None,
) -> PlannerResult:
    """Create a lecture allocation plan.

    ``slots`` must already exclude holidays, examinations, and fixed sessions.
    ``reserved_minutes`` marks capacity already consumed by preserved
    allocations (keyed by slot index); ``earliest_slot`` prevents a topic from
    starting before a given slot index, which keeps prerequisite ordering when
    part of the schedule is preserved during recovery.

    ``preseeded`` carries allocations that already exist and must be kept, keyed
    by slot index. They occupy their slot's capacity, count towards the topic's
    remaining effort, and keep their position and sequence. They are the
    mechanism behind "recalculate only the affected part of the schedule".

    ``min_allocation_minutes`` and ``max_topics_per_slot`` express course-level
    preferences: how short a fragment may be, and whether short topics may share
    a lecture.
    """

    reserved = reserved_minutes or {}
    earliest = earliest_slot or {}
    seeded = preseeded or {}
    by_id = _validate_topics(topics)
    ordered_slots = sorted(slots, key=lambda slot: (slot.start, slot.index))
    if any(slot.capacity_minutes <= 0 for slot in ordered_slots):
        raise InvalidTopicError("Every lecture slot must have positive capacity.")
    effective_minimum = max(1, min_allocation_minutes)
    if max_topics_per_slot is not None and max_topics_per_slot < 1:
        raise InvalidTopicError("A lecture must allow at least one topic.")

    total_estimated = sum(topic.estimated_minutes for topic in by_id.values())
    total_completed = sum(
        min(topic.completed_minutes, topic.estimated_minutes) for topic in by_id.values()
    )
    pending = {topic.id: topic.remaining_minutes for topic in by_id.values()}
    warnings: list[str] = []

    for allocations in seeded.values():
        for allocation in allocations:
            topic = by_id.get(allocation.topic_id)
            if topic is None:
                raise InvalidTopicError(
                    f"A preserved allocation references unknown topic {allocation.topic_id}."
                )
            if allocation.minutes <= 0:
                raise InvalidTopicError("Every preserved allocation must have positive minutes.")
            if pending[topic.id] < allocation.minutes:
                warnings.append(
                    f"'{topic.title}' had more minutes preserved than its remaining work, "
                    "so the surplus was discarded."
                )
                pending[topic.id] = 0
            else:
                pending[topic.id] -= allocation.minutes
    preserved_minutes = sum(
        allocation.minutes for allocations in seeded.values() for allocation in allocations
    )

    active_topics = {topic.id: topic for topic in by_id.values() if pending[topic.id] > 0}
    active_order = topological_order(active_topics)

    unsatisfied = {
        topic.id: sum(1 for prereq in topic.prerequisite_ids if pending.get(prereq, 0) > 0)
        for topic in active_topics.values()
    }
    dependents: dict[UUID, list[UUID]] = {topic_id: [] for topic_id in active_topics}
    for topic in active_topics.values():
        for prerequisite_id in topic.prerequisite_ids:
            if prerequisite_id in dependents:
                dependents[prerequisite_id].append(topic.id)

    ready: list[PlannerTopic] = sorted(
        (topic for topic in active_topics.values() if unsatisfied[topic.id] == 0),
        key=lambda topic: topic.sort_key,
    )

    slot_plans = [SlotPlan(slot=slot) for slot in ordered_slots]
    first_allocation: dict[UUID, datetime] = {}
    last_allocation: dict[UUID, datetime] = {}
    split_counts: dict[UUID, int] = {}

    def allocate(slot_plan: SlotPlan, topic_id: UUID, minutes: int) -> None:
        sequence = len(slot_plan.allocations) + 1
        slot_plan.allocations.append(
            PlannedAllocation(topic_id=topic_id, minutes=minutes, sequence_in_session=sequence)
        )
        pending[topic_id] -= minutes
        split_counts[topic_id] = split_counts.get(topic_id, 0) + 1
        first_allocation.setdefault(topic_id, slot_plan.slot.start)
        last_allocation[topic_id] = slot_plan.slot.end

    def release_dependents(topic_id: UUID) -> None:
        for dependent_id in dependents.get(topic_id, []):
            unsatisfied[dependent_id] -= 1
            if unsatisfied[dependent_id] == 0 and pending[dependent_id] > 0:
                ready.append(active_topics[dependent_id])
        ready.sort(key=lambda topic: topic.sort_key)

    for slot_plan in slot_plans:
        seeded_allocations = seeded.get(slot_plan.slot.index, [])
        if seeded_allocations:
            slot_plan.allocations.extend(seeded_allocations)
            for allocation in seeded_allocations:
                first_allocation.setdefault(allocation.topic_id, slot_plan.slot.start)
                last_allocation[allocation.topic_id] = slot_plan.slot.end
        blocked_minutes = max(
            reserved.get(slot_plan.slot.index, 0),
            sum(allocation.minutes for allocation in seeded_allocations),
        )
        capacity = slot_plan.slot.capacity_minutes - max(0, blocked_minutes)
        while capacity >= effective_minimum and ready:
            if (
                max_topics_per_slot is not None
                and len(slot_plan.allocations) >= max_topics_per_slot
            ):
                break
            allowed = [
                topic for topic in ready if earliest.get(topic.id, 0) <= slot_plan.slot.index
            ]
            if not allowed:
                break
            head = allowed[0]

            if pending[head.id] <= capacity:
                minutes = pending[head.id]
                ready.remove(head)
                allocate(slot_plan, head.id, minutes)
                capacity -= minutes
                release_dependents(head.id)
                continue

            if head.is_splittable:
                # Fill the slot with the current topic; it remains ready.
                allocate(slot_plan, head.id, capacity)
                capacity = 0
                break

            # The head cannot fit and must not be split. Try a later ready topic
            # that fits, preserving the head for a larger future slot.
            fitting = next(
                (topic for topic in allowed[1:] if pending[topic.id] <= capacity),
                None,
            )
            if fitting is None:
                break
            fitting_minutes = pending[fitting.id]
            ready.remove(fitting)
            allocate(slot_plan, fitting.id, fitting_minutes)
            capacity -= fitting_minutes
            release_dependents(fitting.id)

    scheduled_minutes = sum(plan.allocated_minutes for plan in slot_plans)
    unplaced: list[UnplacedTopic] = []
    max_slot_capacity = max((slot.capacity_minutes for slot in ordered_slots), default=0)
    unscheduled_minutes = sum(pending.values())
    for allocations in seeded.values():
        for allocation in allocations:
            split_counts[allocation.topic_id] = split_counts.get(allocation.topic_id, 0) + 1

    for topic in active_order:
        remaining = pending[topic.id]
        if remaining <= 0:
            continue
        if not topic.is_splittable and remaining > max_slot_capacity:
            reason = (
                f"Topic is marked as non-splittable and needs {remaining} minutes, but the "
                f"longest available lecture slot is {max_slot_capacity} minutes."
            )
        elif unscheduled_minutes > 0:
            reason = (
                f"{remaining} minutes of teaching effort could not be placed in the remaining "
                "lecture slots."
            )
        else:
            reason = "Topic could not be scheduled."
        unplaced.append(
            UnplacedTopic(
                topic_id=topic.id,
                title=topic.title,
                remaining_minutes=remaining,
                reason=reason,
            )
        )

    if unplaced:
        shortfall = sum(item.remaining_minutes for item in unplaced)
        additional = math.ceil(shortfall / max_slot_capacity) if max_slot_capacity > 0 else None
        warnings.append(
            f"The available teaching calendar is insufficient by {shortfall} minutes "
            f"({additional} additional lecture(s) at {max_slot_capacity} minutes each)."
        )
        if any(
            not active_topics[item.topic_id].is_splittable
            and item.remaining_minutes > max_slot_capacity
            for item in unplaced
        ):
            warnings.append(
                "At least one topic is too long for a single lecture and is marked "
                "non-splittable. Increase the lecture duration or allow splitting."
            )

    used_slots = [plan for plan in slot_plans if plan.allocations]
    capacity_total = sum(plan.slot.capacity_minutes for plan in slot_plans)
    projected_completion = max((plan.slot.end for plan in used_slots), default=None)
    total_splits = sum(1 for count in split_counts.values() if count > 1)
    additional_sessions_required = (
        math.ceil(unscheduled_minutes / max_slot_capacity)
        if unscheduled_minutes > 0 and max_slot_capacity > 0
        else 0
    )

    metrics: dict[str, object] = {
        "total_estimated_minutes": total_estimated,
        "completed_minutes": total_completed,
        "preserved_minutes": preserved_minutes,
        "remaining_minutes": total_estimated - total_completed - preserved_minutes,
        "scheduled_minutes": scheduled_minutes,
        "unscheduled_minutes": unscheduled_minutes,
        "capacity_minutes": capacity_total,
        "idle_minutes": capacity_total - scheduled_minutes,
        "utilization_percent": round(
            (scheduled_minutes / capacity_total * 100) if capacity_total else 0.0, 1
        ),
        "coverage_percent": round(
            (total_completed / total_estimated * 100) if total_estimated else 100.0, 1
        ),
        "projected_coverage_percent": round(
            ((total_completed + scheduled_minutes) / total_estimated * 100)
            if total_estimated
            else 100.0,
            1,
        ),
        "topic_split_count": total_splits,
        "allocation_count": sum(len(plan.allocations) for plan in slot_plans),
        "sessions_used": len(used_slots),
        "sessions_available": len(slot_plans),
        "additional_sessions_required": additional_sessions_required,
        "expected_completion": projected_completion.isoformat() if projected_completion else None,
        "is_feasible": not unplaced,
        "quality_score": round(
            max(
                0.0,
                100.0
                - total_splits * 5.0
                - (capacity_total - scheduled_minutes) / max(1, max_slot_capacity) * 1.0,
            ),
            1,
        ),
    }

    return PlannerResult(
        slots=slot_plans,
        unplaced_topics=unplaced,
        warnings=warnings,
        metrics=metrics,
        first_allocation=first_allocation,
        last_allocation=last_allocation,
    )


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "error"
    topic_id: UUID | None = None
    slot_index: int | None = None


def validate_plan(
    topics: Iterable[PlannerTopic],
    result: PlannerResult,
) -> list[ValidationIssue]:
    """Validate a produced plan against the hard constraints.

    This is deliberately independent from the construction algorithm so that a
    regression in the planner is caught before a revision is persisted.
    """

    by_id = {topic.id: topic for topic in topics}
    issues: list[ValidationIssue] = []
    seen_per_slot: dict[int, set[UUID]] = {}
    first_position: dict[UUID, tuple[datetime, int]] = {}
    last_position: dict[UUID, tuple[datetime, int]] = {}

    for slot_plan in result.slots:
        slot = slot_plan.slot
        total = 0
        seen = seen_per_slot.setdefault(slot.index, set())
        for allocation in slot_plan.allocations:
            topic = by_id.get(allocation.topic_id)
            if topic is None:
                issues.append(
                    ValidationIssue(
                        code="unknown_topic",
                        message="Allocation references a topic outside the course.",
                        topic_id=allocation.topic_id,
                        slot_index=slot.index,
                    )
                )
                continue
            if allocation.minutes <= 0:
                issues.append(
                    ValidationIssue(
                        code="non_positive_allocation",
                        message=f"Allocation for '{topic.title}' must be positive.",
                        topic_id=topic.id,
                        slot_index=slot.index,
                    )
                )
            if allocation.topic_id in seen:
                issues.append(
                    ValidationIssue(
                        code="duplicate_allocation",
                        message=f"Topic '{topic.title}' is allocated twice in the same lecture.",
                        topic_id=topic.id,
                        slot_index=slot.index,
                    )
                )
            seen.add(allocation.topic_id)
            total += allocation.minutes
            position = (slot.start, allocation.sequence_in_session)
            first_position.setdefault(topic.id, position)
            last_position[topic.id] = position
        if total > slot.capacity_minutes:
            issues.append(
                ValidationIssue(
                    code="slot_over_capacity",
                    message=(
                        f"Lecture on {slot.start.isoformat()} is over capacity "
                        f"({total} > {slot.capacity_minutes} minutes)."
                    ),
                    slot_index=slot.index,
                )
            )

    for topic in by_id.values():
        if topic.id not in first_position:
            continue
        for prerequisite_id in topic.prerequisite_ids:
            prerequisite = by_id.get(prerequisite_id)
            if prerequisite is None:
                issues.append(
                    ValidationIssue(
                        code="unknown_prerequisite",
                        message=f"Topic '{topic.title}' has an unknown prerequisite.",
                        topic_id=topic.id,
                    )
                )
                continue
            if prerequisite.remaining_minutes == 0:
                continue
            prerequisite_position = last_position.get(prerequisite_id)
            if prerequisite_position is None:
                issues.append(
                    ValidationIssue(
                        code="prerequisite_unscheduled",
                        message=(
                            f"Topic '{topic.title}' is scheduled before its prerequisite "
                            f"'{prerequisite.title}'."
                        ),
                        topic_id=topic.id,
                    )
                )
            elif prerequisite_position >= first_position[topic.id]:
                issues.append(
                    ValidationIssue(
                        code="prerequisite_order",
                        message=(
                            f"Topic '{topic.title}' starts before prerequisite "
                            f"'{prerequisite.title}' is complete."
                        ),
                        topic_id=topic.id,
                    )
                )

    for unplaced in result.unplaced_topics:
        issues.append(
            ValidationIssue(
                code="unplaced_topic",
                message=f"'{unplaced.title}' could not be placed: {unplaced.reason}",
                topic_id=unplaced.topic_id,
                severity="warning",
            )
        )
    return issues
