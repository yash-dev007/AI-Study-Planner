"""Turn availability rules and calendar exceptions into lecture slots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.scheduling.planner import PlannerSlot


@dataclass(frozen=True, slots=True)
class AvailabilityWindow:
    weekday: int
    start_time: time
    end_time: time
    room: str = ""
    effective_from: date | None = None
    effective_until: date | None = None
    is_enabled: bool = True


@dataclass(frozen=True, slots=True)
class CalendarBlock:
    date: date
    blocks_teaching: bool = True
    description: str = ""


@dataclass(frozen=True, slots=True)
class OccupiedInterval:
    """A fixed session that must not be double-booked (e.g. a completed lecture)."""

    start: datetime
    end: datetime


class CalendarConfigurationError(ValueError):
    pass


def resolve_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CalendarConfigurationError(f"Unknown timezone: {name!r}") from exc


def build_slots(
    *,
    start_date: date,
    end_date: date,
    timezone: str,
    lecture_duration_minutes: int,
    windows: list[AvailabilityWindow],
    blocks: list[CalendarBlock] | None = None,
    occupied: list[OccupiedInterval] | None = None,
    not_before: datetime | None = None,
    max_sessions: int | None = None,
) -> list[PlannerSlot]:
    """Expand weekly windows into concrete, non-overlapping lecture slots.

    ``not_before`` excludes slots that have already started. ``occupied``
    removes overlap with fixed sessions (for example completed lectures kept
    from a previous revision). Blocks with ``blocks_teaching`` remove the day.
    """

    if end_date < start_date:
        raise CalendarConfigurationError("The semester end date precedes its start date.")
    if lecture_duration_minutes <= 0:
        raise CalendarConfigurationError("Lecture duration must be positive.")

    tz = resolve_timezone(timezone)
    blocked_dates = {block.date for block in (blocks or []) if block.blocks_teaching}
    occupied_intervals = sorted(occupied or [], key=lambda item: item.start)
    active_windows = [window for window in windows if window.is_enabled]

    if not active_windows:
        raise CalendarConfigurationError(
            "No weekly availability is configured, so no lecture slots can be generated."
        )

    slots: list[PlannerSlot] = []
    current = start_date
    index = 0
    while current <= end_date:
        if current not in blocked_dates:
            weekday = current.weekday()
            for window in active_windows:
                if window.weekday != weekday:
                    continue
                if window.effective_from and current < window.effective_from:
                    continue
                if window.effective_until and current > window.effective_until:
                    continue

                cursor = datetime.combine(current, window.start_time, tzinfo=tz)
                window_end = datetime.combine(current, window.end_time, tzinfo=tz)
                if window_end <= cursor:
                    continue
                while cursor + timedelta(minutes=lecture_duration_minutes) <= window_end:
                    slot_start = cursor
                    slot_end = cursor + timedelta(minutes=lecture_duration_minutes)
                    cursor = slot_end

                    if not_before is not None and slot_start < not_before:
                        continue
                    if any(
                        slot_start < interval.end and slot_end > interval.start
                        for interval in occupied_intervals
                    ):
                        continue

                    slots.append(
                        PlannerSlot(
                            index=index,
                            start=slot_start,
                            end=slot_end,
                            capacity_minutes=lecture_duration_minutes,
                        )
                    )
                    index += 1
                    if max_sessions is not None and len(slots) >= max_sessions:
                        return slots
        current += timedelta(days=1)

    return slots


def count_available_sessions(
    *,
    start_date: date,
    end_date: date,
    windows: list[AvailabilityWindow],
    lecture_duration_minutes: int,
    blocks: list[CalendarBlock] | None = None,
) -> int:
    """Cheap capacity estimate used for validation and dashboard hints."""

    blocked_dates = {block.date for block in (blocks or []) if block.blocks_teaching}
    total = 0
    current = start_date
    while current <= end_date:
        if current not in blocked_dates:
            weekday = current.weekday()
            for window in windows:
                if not window.is_enabled or window.weekday != weekday:
                    continue
                if window.effective_from and current < window.effective_from:
                    continue
                if window.effective_until and current > window.effective_until:
                    continue
                start_minutes = window.start_time.hour * 60 + window.start_time.minute
                end_minutes = window.end_time.hour * 60 + window.end_time.minute
                total += max(0, (end_minutes - start_minutes) // lecture_duration_minutes)
        current += timedelta(days=1)
    return total
