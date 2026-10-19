from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from app.domain.scheduling.calendar import (
    AvailabilityWindow,
    CalendarBlock,
    CalendarConfigurationError,
    OccupiedInterval,
    build_slots,
    count_available_sessions,
)
from app.domain.scheduling.diff import diff_results
from app.domain.scheduling.planner import (
    DependencyCycleError,
    PlannerSlot,
    PlannerTopic,
    plan_schedule,
    validate_plan,
)

UTC = ZoneInfo("UTC")
BASE = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def topic(
    name: str, minutes: int, *, seq: int = 1, unit: int = 1, prereqs=(), splittable=True, priority=3
):
    return PlannerTopic(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, name),
        key=name,
        title=name,
        unit_sequence=unit,
        sequence=seq,
        estimated_minutes=minutes,
        is_splittable=splittable,
        priority=priority,
        prerequisite_ids=frozenset(uuid.uuid5(uuid.NAMESPACE_DNS, p) for p in prereqs),
    )


def slots(count: int, minutes: int = 60, start: datetime = BASE) -> list[PlannerSlot]:
    return [
        PlannerSlot(
            index=i,
            start=start + timedelta(days=i),
            end=start + timedelta(days=i, minutes=minutes),
            capacity_minutes=minutes,
        )
        for i in range(count)
    ]


class TestPlanner:
    def test_fills_slots_in_order_and_combines_short_topics(self):
        topics = [topic("A", 30), topic("B", 30, seq=2), topic("C", 60, seq=3)]
        result = plan_schedule(topics, slots(3))
        assert result.is_feasible
        assert result.metrics["scheduled_minutes"] == 120
        first = result.slots[0]
        assert [a.minutes for a in first.allocations] == [30, 30]
        assert result.slots[1].allocations[0].minutes == 60
        assert result.metrics["allocation_count"] == 3

    def test_splits_long_topic_across_slots(self):
        result = plan_schedule([topic("A", 150)], slots(3))
        assert result.is_feasible
        assert [p.planned_minutes for p in result.slots] == [60, 60, 30]
        assert result.metrics["topic_split_count"] == 1
        assert result.metrics["expected_completion"]

    def test_prerequisite_ordering_is_respected(self):
        result = plan_schedule(
            [topic("B", 60, seq=1, prereqs=["A"]), topic("A", 60, seq=2)], slots(2)
        )
        assert result.is_feasible
        assert (
            result.first_allocation[uuid.uuid5(uuid.NAMESPACE_DNS, "A")]
            < result.first_allocation[uuid.uuid5(uuid.NAMESPACE_DNS, "B")]
        )

    def test_completed_prerequisite_is_already_satisfied(self):
        b = topic("B", 60, prereqs=["A"])
        completed_a = PlannerTopic(
            id=uuid.uuid5(uuid.NAMESPACE_DNS, "A"),
            key="A",
            title="A",
            unit_sequence=1,
            sequence=1,
            estimated_minutes=60,
            completed_minutes=60,
        )
        result = plan_schedule([completed_a, b], slots(1))
        assert result.is_feasible
        assert result.slots[0].allocations[0].topic_id == b.id

    def test_cycle_is_rejected(self):
        with pytest.raises(DependencyCycleError):
            plan_schedule([topic("A", 60, prereqs=["B"]), topic("B", 60, prereqs=["A"])], slots(2))

    def test_infeasible_capacity_is_reported_not_faked(self):
        result = plan_schedule([topic("A", 120), topic("B", 60)], slots(2, minutes=60))
        assert not result.is_feasible
        assert result.metrics["unscheduled_minutes"] == 60
        assert result.metrics["additional_sessions_required"] == 1
        assert {item.title for item in result.unplaced_topics} == {"A"}
        assert any("insufficient" in warning for warning in result.warnings)

    def test_never_duplicates_allocation_in_a_slot(self):
        result = plan_schedule(
            [topic("A", 30), topic("B", 30), topic("C", 30)], slots(1, minutes=60)
        )
        assert not result.is_feasible
        assert len(result.slots[0].allocations) == 2
        assert len({a.topic_id for a in result.slots[0].allocations}) == 2

    def test_non_splittable_topic_is_deferred_to_larger_slot(self):
        small_then_large = [
            PlannerSlot(index=0, start=BASE, end=BASE + timedelta(minutes=30), capacity_minutes=30),
            PlannerSlot(
                index=1,
                start=BASE + timedelta(days=1),
                end=BASE + timedelta(days=1, minutes=90),
                capacity_minutes=90,
            ),
        ]
        result = plan_schedule(
            [topic("Big", 90, splittable=False), topic("Small", 30, seq=2)], small_then_large
        )
        assert result.is_feasible
        assert result.slots[1].allocations[0].topic_id == uuid.uuid5(uuid.NAMESPACE_DNS, "Big")

    def test_validate_plan_accepts_generated_plan(self):
        topics = [topic("A", 90), topic("B", 30, prereqs=["A"])]
        result = plan_schedule(topics, slots(3))
        assert validate_plan(topics, result) == []

    def test_validate_plan_detects_prerequisite_violation(self):
        topics = [topic("A", 60), topic("B", 60, prereqs=["A"])]
        result = plan_schedule(topics, slots(2))
        result.slots[0].allocations, result.slots[1].allocations = (
            result.slots[1].allocations,
            result.slots[0].allocations,
        )
        issues = validate_plan(topics, result)
        assert any(issue.code == "prerequisite_order" for issue in issues)

    def test_fully_completed_topic_is_not_rescheduled(self):
        finished = PlannerTopic(
            id=uuid.uuid5(uuid.NAMESPACE_DNS, "A"),
            key="A",
            title="A",
            unit_sequence=1,
            sequence=1,
            estimated_minutes=60,
            completed_minutes=60,
        )
        result = plan_schedule([finished], slots(1))
        assert result.is_feasible
        assert result.metrics["scheduled_minutes"] == 0
        assert result.metrics["coverage_percent"] == 100.0

    def test_partial_progress_reduces_remaining_work(self):
        partial = PlannerTopic(
            id=uuid.uuid5(uuid.NAMESPACE_DNS, "A"),
            key="A",
            title="A",
            unit_sequence=1,
            sequence=1,
            estimated_minutes=90,
            completed_minutes=30,
        )
        result = plan_schedule([partial], slots(2))
        assert result.metrics["scheduled_minutes"] == 60
        assert result.metrics["coverage_percent"] == 33.3


class TestCalendar:
    def _window(self, weekday=0, start=time(9), end=time(11)):
        return AvailabilityWindow(weekday=weekday, start_time=start, end_time=end)

    def test_expands_windows_into_slots(self):
        result = build_slots(
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 18),
            timezone="UTC",
            lecture_duration_minutes=60,
            windows=[self._window()],
        )
        assert len(result) == 4
        assert result[0].start == datetime(2026, 1, 5, 9, tzinfo=UTC)
        assert result[1].start == datetime(2026, 1, 5, 10, tzinfo=UTC)

    def test_holidays_are_excluded(self):
        result = build_slots(
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 18),
            timezone="UTC",
            lecture_duration_minutes=60,
            windows=[self._window()],
            blocks=[CalendarBlock(date(2026, 1, 12))],
        )
        assert len(result) == 2

    def test_not_before_filters_past_slots(self):
        result = build_slots(
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 18),
            timezone="UTC",
            lecture_duration_minutes=60,
            windows=[self._window()],
            not_before=datetime(2026, 1, 12, 9, 30, tzinfo=UTC),
        )
        assert all(slot.start >= datetime(2026, 1, 12, 9, 30, tzinfo=UTC) for slot in result)
        assert len(result) == 1

    def test_occupied_interval_is_removed(self):
        result = build_slots(
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
            timezone="UTC",
            lecture_duration_minutes=60,
            windows=[self._window()],
            occupied=[
                OccupiedInterval(
                    datetime(2026, 1, 5, 9, tzinfo=UTC), datetime(2026, 1, 5, 10, tzinfo=UTC)
                )
            ],
        )
        assert len(result) == 1
        assert result[0].start.hour == 10

    def test_missing_windows_raises(self):
        with pytest.raises(CalendarConfigurationError):
            build_slots(
                start_date=date(2026, 1, 5),
                end_date=date(2026, 1, 6),
                timezone="UTC",
                lecture_duration_minutes=60,
                windows=[],
            )

    def test_capacity_count(self):
        assert (
            count_available_sessions(
                start_date=date(2026, 1, 5),
                end_date=date(2026, 1, 11),
                windows=[self._window()],
                lecture_duration_minutes=60,
            )
            == 2
        )


class TestDiff:
    def test_initial_generation_reports_added(self):
        topics = [topic("A", 60)]
        result = plan_schedule(topics, slots(1))
        diff = diff_results(None, result, {topics[0].id: "A"}, reason="initial")
        assert len(diff.added) == 1
        assert diff.changes[0].change_type == "added"

    def test_moved_allocation_is_detected(self):
        topics = [topic("A", 60)]
        first = plan_schedule(topics, slots(1))
        later = plan_schedule(topics, slots(1, start=BASE + timedelta(days=3)))
        diff = diff_results(first, later, {topics[0].id: "A"}, reason="missed lecture")
        assert len(diff.moved) == 1
        assert diff.changes[0].change_type == "moved"
