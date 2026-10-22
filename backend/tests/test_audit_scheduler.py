"""Regression tests for the scheduling audit (SCHEDULER team).

These tests pin the hard invariants of the scheduler: deterministic planning,
DST-safe slot generation, the allocation ledger, progress derived only from
recorded delivery, and immutable history across reschedules.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.domain.scheduling.calendar import AvailabilityWindow, build_slots
from app.domain.scheduling.planner import PlannerSlot, PlannerTopic, plan_schedule
from fastapi.testclient import TestClient
from tests.conftest import (
    confirm_syllabus,
    course_payload,
    semester_start,
)

API = "/api/v1"

BASE = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def topic(
    name: str,
    minutes: int,
    *,
    seq: int = 1,
    unit: int = 1,
    priority: int = 3,
    splittable: bool = True,
) -> PlannerTopic:
    return PlannerTopic(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, name),
        key=name,
        title=name,
        unit_sequence=unit,
        sequence=seq,
        estimated_minutes=minutes,
        priority=priority,
        is_splittable=splittable,
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


def plan_signature(result) -> list[tuple[datetime, list[tuple[uuid.UUID, int, int]]]]:
    """Everything a professor would see, in teaching order."""

    return [
        (
            slot_plan.slot.start,
            [
                (item.topic_id, item.minutes, item.sequence_in_session)
                for item in slot_plan.allocations
            ],
        )
        for slot_plan in result.slots
    ]


def first_scheduled(schedule: dict) -> dict:
    rows = [item for item in schedule["lectures"] if item["status"] == "scheduled"]
    assert rows, "expected at least one scheduled lecture"
    return rows[0]


def lecture_minutes(lecture: dict) -> int:
    start = datetime.fromisoformat(lecture["scheduled_start"])
    end = datetime.fromisoformat(lecture["scheduled_end"])
    return int((end - start).total_seconds() // 60)


SMALL_UNITS = [
    {
        "key": "unit-1",
        "title": "Unit 1",
        "description": "",
        "sequence_number": 1,
        "topics": [
            {
                "key": "t1",
                "title": "Alpha",
                "description": "",
                "learning_objectives": [],
                "estimated_duration_minutes": 60,
                "difficulty": "medium",
                "confidence": 0.9,
                "needs_review": False,
                "prerequisite_keys": [],
                "sequence_number": 1,
            },
            {
                "key": "t2",
                "title": "Beta",
                "description": "",
                "learning_objectives": [],
                "estimated_duration_minutes": 60,
                "difficulty": "medium",
                "confidence": 0.9,
                "needs_review": False,
                "prerequisite_keys": [],
                "sequence_number": 2,
            },
        ],
    }
]


def make_course(client: TestClient, headers: dict[str, str], **overrides) -> dict:
    response = client.post(f"{API}/courses", json=course_payload(**overrides), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# 1. Determinism and integer minutes
# --------------------------------------------------------------------------- #


class TestPlannerDeterminism:
    def test_input_order_does_not_change_the_plan(self):
        topics = [
            topic("A", 150, seq=1, priority=2),
            topic("B", 30, seq=2, priority=3),
            topic("C", 90, seq=3, unit=2, priority=3),
            topic("D", 45, seq=4, unit=2, priority=5),
            topic("E", 20, seq=5, priority=3),
        ]
        slot_list = slots(5, minutes=60)

        first = plan_schedule(topics, slot_list)
        second = plan_schedule(list(reversed(topics)), list(reversed(slot_list)))

        assert plan_signature(first) == plan_signature(second)
        assert first.metrics["scheduled_minutes"] == second.metrics["scheduled_minutes"]

    def test_ties_are_broken_deterministically_by_unit_and_sequence(self):
        # Same priority everywhere: unit and sequence order must win.
        topics = [topic("Z", 30, seq=3), topic("Y", 30, seq=1), topic("X", 30, seq=2)]
        result = plan_schedule(topics, slots(1, minutes=90))
        assert [item.topic_id for item in result.slots[0].allocations] == [
            uuid.uuid5(uuid.NAMESPACE_DNS, "Y"),
            uuid.uuid5(uuid.NAMESPACE_DNS, "X"),
            uuid.uuid5(uuid.NAMESPACE_DNS, "Z"),
        ]

    def test_every_planned_minute_is_an_integer_within_capacity(self):
        topics = [
            topic("A", 100, seq=1),
            topic("B", 25, seq=2),
            topic("C", 70, seq=3, splittable=False),
        ]
        slot_list = slots(6, minutes=45)
        result = plan_schedule(topics, slot_list)

        for slot_plan in result.slots:
            plans = slot_plan.allocations
            assert all(isinstance(item.minutes, int) for item in plans)
            assert all(item.minutes > 0 for item in plans)
            assert sum(item.minutes for item in plans) <= slot_plan.slot.capacity_minutes
            assert len({item.topic_id for item in plans}) == len(plans)

        # Nothing is invented: scheduled + unscheduled equals the real demand.
        assert result.scheduled_minutes + result.metrics["unscheduled_minutes"] == 195
        assert isinstance(result.metrics["additional_sessions_required"], int)

    def test_splitting_respects_the_configured_minimum_fragment(self):
        topics = [topic("A", 100)]
        result = plan_schedule(topics, slots(1, minutes=60), min_allocation_minutes=40)
        # A 100-minute topic cannot leave a 20-minute fragment in the next slot.
        assert any(
            item.minutes >= 40 for slot_plan in result.slots for item in slot_plan.allocations
        )
        scheduled = sum(
            item.minutes for slot_plan in result.slots for item in slot_plan.allocations
        )
        assert scheduled >= 60


# --------------------------------------------------------------------------- #
# 2. Calendar: DST transitions must never produce duplicate or empty slots
# --------------------------------------------------------------------------- #


class TestCalendarDst:
    NEW_YORK = "America/New_York"

    def _sunday_slots(self, day: date, *, duration: int = 60) -> list[PlannerSlot]:
        return build_slots(
            start_date=day,
            end_date=day,
            timezone=self.NEW_YORK,
            lecture_duration_minutes=duration,
            windows=[
                AvailabilityWindow(
                    weekday=day.weekday(), start_time=time(0), end_time=time(6)
                )
            ],
        )

    def test_spring_forward_never_duplicates_an_instant(self):
        # 2026-03-08: 02:00 local does not exist, the clock jumps to 03:00.
        result = self._sunday_slots(date(2026, 3, 8))

        instants = [slot.start.astimezone(UTC) for slot in result]
        assert len(set(instants)) == len(instants), "two lectures would start at the same instant"

        for slot in result:
            start = slot.start.astimezone(UTC)
            end = slot.end.astimezone(UTC)
            assert end > start, "a lecture slot must have a real, positive duration"
            # The local start time must exist: converting back reproduces it.
            assert slot.start.astimezone(ZoneInfo(self.NEW_YORK)) == slot.start

    def test_fall_back_never_duplicates_an_instant(self):
        # 2026-11-01: 01:00-02:00 local happens twice.
        result = self._sunday_slots(date(2026, 11, 1))

        instants = [slot.start.astimezone(UTC) for slot in result]
        assert len(set(instants)) == len(instants)
        for slot in result:
            assert slot.end.astimezone(UTC) > slot.start.astimezone(UTC)

    def test_slots_stay_in_local_wall_clock_order(self):
        result = self._sunday_slots(date(2026, 3, 8))
        assert [slot.start.hour for slot in result] == sorted(slot.start.hour for slot in result)
        assert [slot.index for slot in result] == list(range(len(result)))


# --------------------------------------------------------------------------- #
# 3. Allocation ledger through the API
# --------------------------------------------------------------------------- #


class TestAllocationLedger:
    def test_planned_minutes_never_exceed_the_lecture_window(
        self, client: TestClient, scheduled_course: dict
    ) -> None:
        for lecture in scheduled_course["schedule"]["lectures"]:
            window = lecture_minutes(lecture)
            planned = sum(item["planned_minutes"] for item in lecture["allocations"])
            assert planned <= window, (
                f"lecture {lecture['sequence']} plans {planned} of {window} minutes"
            )
            topic_ids = [item["topic_id"] for item in lecture["allocations"]]
            assert len(set(topic_ids)) == len(topic_ids), "duplicate topic in one lecture"
            assert all(item["planned_minutes"] > 0 for item in lecture["allocations"])

    def test_over_delivery_is_capped_at_the_planned_minutes(
        self, client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
    ) -> None:
        course_id = scheduled_course["course_id"]
        lecture = first_scheduled(scheduled_course["schedule"])
        allocation = lecture["allocations"][0]

        response = client.patch(
            f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
            json={
                "status": "completed",
                "delivered_minutes": [
                    {
                        "topic_id": allocation["topic_id"],
                        "minutes": allocation["planned_minutes"] + 45,
                    }
                ],
                "recalculate": False,
            },
            headers=professor_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        recorded = next(
            item
            for item in body["lecture"]["allocations"]
            if item["topic_id"] == allocation["topic_id"]
        )
        assert recorded["actual_minutes"] == allocation["planned_minutes"]
        assert recorded["actual_minutes"] <= recorded["planned_minutes"]
        assert "capped" in body["message"]


# --------------------------------------------------------------------------- #
# 4. Progress derives from actual minutes only
# --------------------------------------------------------------------------- #


class TestProgressDerivation:
    def test_missed_lecture_contributes_zero(
        self, client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
    ) -> None:
        course_id = scheduled_course["course_id"]
        lecture = first_scheduled(scheduled_course["schedule"])

        response = client.patch(
            f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
            json={"status": "missed", "reason": "Professor ill", "recalculate": False},
            headers=professor_headers,
        )
        assert response.status_code == 200, response.text

        progress = client.get(
            f"{API}/courses/{course_id}/progress", headers=professor_headers
        ).json()
        rows = {row["topic_id"]: row for row in progress["topics"]}
        for allocation in lecture["allocations"]:
            row = rows[allocation["topic_id"]]
            assert row["delivered_minutes"] == 0
            assert row["state"] != "completed"
        assert progress["summary"]["delivered_minutes"] == 0
        assert progress["summary"]["missed_lectures"] == 1
        assert progress["summary"]["coverage_percent"] == 0.0

    def test_dashboard_is_safe_before_any_syllabus(
        self, client: TestClient, professor_headers: dict[str, str]
    ) -> None:
        course = make_course(client, professor_headers)
        dashboard = client.get(
            f"{API}/courses/{course['id']}/dashboard", headers=professor_headers
        )
        assert dashboard.status_code == 200, dashboard.text
        body = dashboard.json()
        assert body["total_estimated_minutes"] == 0
        assert body["remaining_minutes"] == 0
        assert body["coverage_percent"] == 0.0

        progress = client.get(
            f"{API}/courses/{course['id']}/progress", headers=professor_headers
        )
        assert progress.status_code == 200, progress.text
        assert progress.json()["topics"] == []


# --------------------------------------------------------------------------- #
# 5. Immutable history and repeat delivery reports
# --------------------------------------------------------------------------- #


class TestHistoryAndIdempotency:
    def test_reporting_completion_twice_does_not_inflate_delivered_minutes(
        self, client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
    ) -> None:
        course_id = scheduled_course["course_id"]
        lecture = first_scheduled(scheduled_course["schedule"])
        target = max(lecture["allocations"], key=lambda item: item["planned_minutes"])
        partial = max(1, target["planned_minutes"] - 10)

        first = client.patch(
            f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
            json={
                "status": "completed",
                "delivered_minutes": [{"topic_id": target["topic_id"], "minutes": partial}],
            },
            headers=professor_headers,
        )
        assert first.status_code == 200, first.text
        assert first.json()["revision"] is not None, "partial coverage must be redistributed"

        before = client.get(
            f"{API}/courses/{course_id}/progress", headers=professor_headers
        ).json()
        delivered_before = {
            row["topic_id"]: row["delivered_minutes"] for row in before["topics"]
        }
        assert delivered_before[target["topic_id"]] == partial

        second = client.patch(
            f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
            json={"status": "completed"},
            headers=professor_headers,
        )
        assert second.status_code == 200, second.text
        assert second.json()["revision"] is None, "a repeated report changed nothing"

        after = client.get(f"{API}/courses/{course_id}/progress", headers=professor_headers).json()
        delivered_after = {row["topic_id"]: row["delivered_minutes"] for row in after["topics"]}
        assert delivered_after == delivered_before

    def test_closed_lecture_survives_and_old_revision_stays_readable(
        self, client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
    ) -> None:
        course_id = scheduled_course["course_id"]
        original_revision = scheduled_course["revision_id"]
        lecture = first_scheduled(scheduled_course["schedule"])

        response = client.patch(
            f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
            json={"status": "missed", "reason": "Campus closed", "recalculate": True},
            headers=professor_headers,
        )
        assert response.status_code == 200, response.text
        proposal_id = response.json()["revision"]["id"]
        assert proposal_id != original_revision

        approved = client.post(
            f"{API}/courses/{course_id}/schedule/revisions/{proposal_id}/approve",
            json={"publish": True, "note": "ok"},
            headers=professor_headers,
        )
        assert approved.status_code == 200, approved.text

        old = client.get(
            f"{API}/courses/{course_id}/schedule/revisions/{original_revision}",
            headers=professor_headers,
        )
        assert old.status_code == 200, "the previously approved revision must stay queryable"
        old_body = old.json()
        assert old_body["revision"]["approval_status"] == "superseded"
        missed_rows = [item for item in old_body["lectures"] if item["status"] == "missed"]
        assert len(missed_rows) == 1
        assert missed_rows[0]["id"] == lecture["id"]
        assert all(item["actual_minutes"] is None for item in missed_rows[0]["allocations"])

        new_body = client.get(
            f"{API}/courses/{course_id}/schedule/revisions/{proposal_id}",
            headers=professor_headers,
        ).json()
        assert all(item["id"] != lecture["id"] for item in new_body["lectures"])
        history = [item for item in new_body["lectures"] if item["status"] == "missed"]
        assert len(history) == 1
        assert all(
            item["completion_status"] == "skipped" for item in history[0]["allocations"]
        )

    def test_editing_a_lecture_of_a_superseded_revision_is_refused(
        self, client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
    ) -> None:
        course_id = scheduled_course["course_id"]
        lecture = first_scheduled(scheduled_course["schedule"])

        response = client.patch(
            f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
            json={"status": "missed", "reason": "Campus closed", "recalculate": True},
            headers=professor_headers,
        )
        proposal_id = response.json()["revision"]["id"]
        approved = client.post(
            f"{API}/courses/{course_id}/schedule/revisions/{proposal_id}/approve",
            json={"publish": True, "note": "ok"},
            headers=professor_headers,
        )
        assert approved.status_code == 200, approved.text

        stale = client.patch(
            f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
            json={"status": "completed", "recalculate": False},
            headers=professor_headers,
        )
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "REVISION_NOT_CURRENT"


# --------------------------------------------------------------------------- #
# 6. Determinism of a full generation run
# --------------------------------------------------------------------------- #


def test_regeneration_from_the_same_base_is_identical(
    client: TestClient, ready_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = ready_course["course_id"]

    first = client.post(
        f"{API}/courses/{course_id}/schedule/generate",
        json={"strategy": "preserve_future"},
        headers=professor_headers,
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"{API}/courses/{course_id}/schedule/generate",
        json={"strategy": "preserve_future"},
        headers=professor_headers,
    )
    assert second.status_code == 201, second.text

    def signature(schedule: dict) -> list[tuple[str, str, list[tuple[str, int, int]]]]:
        return [
            (
                item["scheduled_start"],
                item["scheduled_end"],
                [
                    (allocation["topic_id"], allocation["planned_minutes"],
                     allocation["sequence_in_session"])
                    for allocation in item["allocations"]
                ],
            )
            for item in schedule["lectures"]
        ]

    assert signature(first.json()) == signature(second.json())


# --------------------------------------------------------------------------- #
# 7. Calendar days are grouped in the course timezone
# --------------------------------------------------------------------------- #


def test_calendar_uses_the_course_timezone_for_days(
    client: TestClient, professor_headers: dict[str, str]
) -> None:
    start = semester_start()
    course = make_course(
        client,
        professor_headers,
        timezone="America/New_York",
        availability_rules=[
            {"weekday": 0, "start_time": "19:00:00", "end_time": "21:00:00", "room": "R1"}
        ],
    )
    confirm_syllabus(client, course["id"], professor_headers, units=SMALL_UNITS)

    generated = client.post(
        f"{API}/courses/{course['id']}/schedule/generate",
        json={"strategy": "preserve_future"},
        headers=professor_headers,
    )
    assert generated.status_code == 201, generated.text
    lectures = generated.json()["lectures"]
    assert len(lectures) == 2, generated.json()["unplaced_topics"]

    entries = client.get(
        f"{API}/courses/{course['id']}/lectures/calendar/entries",
        params={
            "range_start": start.isoformat(),
            "range_end": (start + timedelta(days=6)).isoformat(),
        },
        headers=professor_headers,
    ).json()
    assert {entry["date"] for entry in entries} == {start.isoformat()}

    days = client.get(
        f"{API}/courses/{course['id']}/calendar",
        params={
            "view": "week",
            "range_start": start.isoformat(),
            "range_end": (start + timedelta(days=6)).isoformat(),
        },
        headers=professor_headers,
    ).json()["days"]
    assert sum(day["lectures"] for day in days) == 2
    assert days[0]["lectures"] == 2
    assert days[0]["minutes"] == 120


# --------------------------------------------------------------------------- #
# 8. Day-based dashboard and calendar defaults use the course calendar
# --------------------------------------------------------------------------- #


def test_projected_completion_warning_uses_the_course_calendar_day(
    client: TestClient, professor_headers: dict[str, str]
) -> None:
    start = semester_start()
    course = make_course(
        client,
        professor_headers,
        start_date=start.isoformat(),
        end_date=start.isoformat(),
        timezone="America/New_York",
        availability_rules=[
            {
                "weekday": start.weekday(),
                "start_time": "20:00:00",
                "end_time": "21:00:00",
                "room": "R1",
            }
        ],
    )
    confirm_syllabus(client, course["id"], professor_headers, units=SMALL_UNITS)

    generated = client.post(
        f"{API}/courses/{course['id']}/schedule/generate",
        json={"strategy": "preserve_future"},
        headers=professor_headers,
    )
    assert generated.status_code == 201, generated.text
    lecture = first_scheduled(generated.json())
    assert datetime.fromisoformat(lecture["scheduled_end"]).astimezone(UTC).date() > start, (
        "this test only proves anything when the lecture ends on the next UTC day"
    )

    dashboard = client.get(
        f"{API}/courses/{course['id']}/dashboard", headers=professor_headers
    ).json()
    warnings = dashboard["warnings"]
    assert not any("after the end of the semester" in warning for warning in warnings), warnings


def test_default_calendar_day_is_the_course_local_today(
    client: TestClient, professor_headers: dict[str, str], monkeypatch
) -> None:
    course = make_course(client, professor_headers, timezone="America/New_York")
    # 23:00 on 2026-10-25 in New York is already 2026-10-26 in UTC.
    fixed = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)
    monkeypatch.setattr("app.api.routes.progress.now_utc", lambda: fixed)

    response = client.get(
        f"{API}/courses/{course['id']}/calendar",
        params={"view": "day"},
        headers=professor_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["range_start"] == "2026-10-25"
    assert body["range_end"] == "2026-10-25"


def test_dashboard_capacity_counts_only_free_slots(
    client: TestClient, professor_headers: dict[str, str]
) -> None:
    start = semester_start()
    course = make_course(
        client,
        professor_headers,
        start_date=start.isoformat(),
        end_date=(start + timedelta(days=13)).isoformat(),
        availability_rules=[
            {"weekday": 0, "start_time": "09:00:00", "end_time": "12:00:00", "room": "R1"}
        ],
    )
    confirm_syllabus(client, course["id"], professor_headers, units=SMALL_UNITS)

    generated = client.post(
        f"{API}/courses/{course['id']}/schedule/generate",
        json={"strategy": "preserve_future"},
        headers=professor_headers,
    )
    assert generated.status_code == 201, generated.text
    schedule = generated.json()
    assert len(schedule["lectures"]) == 2

    completed = client.patch(
        f"{API}/courses/{course['id']}/lectures/{first_scheduled(schedule)['id']}/status",
        json={"status": "completed"},
        headers=professor_headers,
    )
    assert completed.status_code == 200, completed.text

    dashboard = client.get(
        f"{API}/courses/{course['id']}/dashboard", headers=professor_headers
    ).json()
    # Two Mondays with three slots each, minus the two lectures that hold a slot
    # (one completed, one scheduled). Blocking a whole day per closed lecture and
    # subtracting the future lectures again would report two free slots instead.
    assert dashboard["capacity_sessions_remaining"] == 4, dashboard["warnings"]
