"""Timezone-safe datetime helpers.

Everything that is persisted is stored in UTC. SQLite does not preserve
timezone information, so values read back from the database are naive. These
helpers make that explicit instead of relying on the comparison behaviour of
mixed naive/aware datetimes, which raises ``TypeError`` in Python.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta


def now_utc() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` as an aware UTC datetime.

    Naive values are assumed to already be UTC (that is how they were stored).
    """

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def combine_utc(day: date, moment: time) -> datetime:
    return datetime.combine(day, moment, tzinfo=UTC)


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = combine_utc(day, time.min)
    return start, start + timedelta(days=1)


def as_utc_iso(value: datetime | None) -> str | None:
    return ensure_utc(value).isoformat() if value is not None else None
