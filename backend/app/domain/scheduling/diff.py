"""Diff two schedules to explain exactly what moved and why."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from app.domain.scheduling.planner import PlannerResult


@dataclass(frozen=True, slots=True)
class AllocationRef:
    topic_id: UUID
    topic_title: str
    session_start: datetime
    minutes: int


@dataclass
class ChangeRecord:
    change_type: str
    topic_id: UUID | None
    topic_title: str
    previous_date: datetime | None
    new_date: datetime | None
    previous_minutes: int | None
    new_minutes: int | None
    change_reason: str


@dataclass
class ScheduleDiff:
    changes: list[ChangeRecord] = field(default_factory=list)
    added: list[AllocationRef] = field(default_factory=list)
    removed: list[AllocationRef] = field(default_factory=list)
    moved: list[tuple[AllocationRef, AllocationRef]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.changes:
            return "No allocation changes."
        parts = []
        if self.added:
            parts.append(f"{len(self.added)} new allocation(s)")
        if self.removed:
            parts.append(f"{len(self.removed)} removed allocation(s)")
        if self.moved:
            parts.append(f"{len(self.moved)} moved allocation(s)")
        return ", ".join(parts) + "."


def _flatten(result: PlannerResult, titles: dict[UUID, str]) -> dict[tuple[UUID, datetime], int]:
    flattened: dict[tuple[UUID, datetime], int] = {}
    for slot_plan in result.slots:
        for allocation in slot_plan.allocations:
            key = (allocation.topic_id, slot_plan.slot.start)
            flattened[key] = flattened.get(key, 0) + allocation.minutes
    return flattened


def diff_results(
    previous: PlannerResult | None,
    proposed: PlannerResult,
    titles: dict[UUID, str],
    *,
    reason: str,
) -> ScheduleDiff:
    diff = ScheduleDiff()
    if previous is None:
        for slot_plan in proposed.slots:
            for allocation in slot_plan.allocations:
                ref = AllocationRef(
                    topic_id=allocation.topic_id,
                    topic_title=titles.get(allocation.topic_id, "Unknown topic"),
                    session_start=slot_plan.slot.start,
                    minutes=allocation.minutes,
                )
                diff.added.append(ref)
                diff.changes.append(
                    ChangeRecord(
                        change_type="added",
                        topic_id=ref.topic_id,
                        topic_title=ref.topic_title,
                        previous_date=None,
                        new_date=ref.session_start,
                        previous_minutes=None,
                        new_minutes=ref.minutes,
                        change_reason="Initial schedule generation.",
                    )
                )
        return diff

    before = _flatten(previous, titles)
    after = _flatten(proposed, titles)

    raw_added = [
        AllocationRef(topic_id, titles.get(topic_id, "Unknown topic"), start, after[key])
        for key in sorted(after.keys() - before.keys(), key=lambda item: (item[1], str(item[0])))
        for topic_id, start in [key]
    ]
    raw_removed = [
        AllocationRef(topic_id, titles.get(topic_id, "Unknown topic"), start, before[key])
        for key in sorted(before.keys() - after.keys(), key=lambda item: (item[1], str(item[0])))
        for topic_id, start in [key]
    ]

    # Pair removed and added allocations of the same topic as moves. A topic can
    # legitimately be split, so pair greedily in chronological order.
    unmatched_added = list(raw_added)
    unmatched_removed: list[AllocationRef] = []
    for removed in raw_removed:
        match_index = next(
            (
                index
                for index, added in enumerate(unmatched_added)
                if added.topic_id == removed.topic_id
            ),
            None,
        )
        if match_index is None:
            unmatched_removed.append(removed)
            continue
        added = unmatched_added.pop(match_index)
        diff.moved.append((removed, added))
        diff.changes.append(
            ChangeRecord(
                change_type="moved",
                topic_id=removed.topic_id,
                topic_title=removed.topic_title,
                previous_date=removed.session_start,
                new_date=added.session_start,
                previous_minutes=removed.minutes,
                new_minutes=added.minutes,
                change_reason=reason,
            )
        )

    for ref in unmatched_added:
        diff.added.append(ref)
        diff.changes.append(
            ChangeRecord(
                change_type="added",
                topic_id=ref.topic_id,
                topic_title=ref.topic_title,
                previous_date=None,
                new_date=ref.session_start,
                previous_minutes=None,
                new_minutes=ref.minutes,
                change_reason=reason,
            )
        )

    for ref in unmatched_removed:
        diff.removed.append(ref)
        diff.changes.append(
            ChangeRecord(
                change_type="removed",
                topic_id=ref.topic_id,
                topic_title=ref.topic_title,
                previous_date=ref.session_start,
                new_date=None,
                previous_minutes=ref.minutes,
                new_minutes=None,
                change_reason=reason,
            )
        )

    diff.changes.sort(
        key=lambda change: (
            change.new_date or change.previous_date or datetime.min.replace(tzinfo=UTC),
            change.topic_title,
        )
    )
    return diff
