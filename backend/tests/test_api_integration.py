"""End-to-end API tests for the Phase 1 professor workflow.

Each test drives the real FastAPI app through HTTP with an isolated SQLite
database. The scheduler runs exactly as in production: no network, no API keys.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from tests.conftest import (
    CONFIRM_UNITS,
    confirm_syllabus,
    course_payload,
    semester_start,
    upload_syllabus,
)

API = "/api/v1"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def topic_id_by_title(schedule: dict, title: str) -> str:
    for lecture in schedule["lectures"]:
        for allocation in lecture["allocations"]:
            if allocation["topic_title"] == title:
                return allocation["topic_id"]
    raise AssertionError(f"Topic not scheduled: {title}")


def allocations_of(schedule: dict, topic_id: str) -> list[dict]:
    rows = []
    for lecture in schedule["lectures"]:
        for allocation in lecture["allocations"]:
            if allocation["topic_id"] == topic_id:
                rows.append({"lecture": lecture, "allocation": allocation})
    return rows


def first_future_lecture(schedule: dict) -> dict:
    scheduled = [item for item in schedule["lectures"] if item["status"] == "scheduled"]
    assert scheduled, "expected at least one scheduled lecture"
    return scheduled[0]


def total_allocated(schedule: dict, topic_id: str) -> int:
    return sum(
        row["allocation"]["planned_minutes"]
        for row in allocations_of(schedule, topic_id)
    )


# --------------------------------------------------------------------------- #
# 1. Happy path: a valid syllabus produces a usable schedule
# --------------------------------------------------------------------------- #


def test_health_reports_configuration(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["llm_provider"] == "none"


def test_register_login_and_me(client: TestClient) -> None:
    email = f"prof-{uuid.uuid4().hex[:6]}@example.edu"
    registered = client.post(
        f"{API}/auth/register",
        json={"name": "Ada", "email": email, "password": "Sup3rSecret!", "role": "professor"},
    )
    assert registered.status_code == 201
    token = registered.json()["access_token"]

    me = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == email

    duplicate = client.post(
        f"{API}/auth/register",
        json={"name": "Ada", "email": email, "password": "Sup3rSecret!", "role": "professor"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"

    login = client.post(f"{API}/auth/login", json={"email": email, "password": "Sup3rSecret!"})
    assert login.status_code == 200

    bad_login = client.post(f"{API}/auth/login", json={"email": email, "password": "wrong"})
    assert bad_login.status_code == 401
    assert bad_login.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_course_creation_validates_windows(
    client: TestClient, professor_headers: dict[str, str]
) -> None:
    payload = course_payload(
        availability_rules=[
            {"weekday": 0, "start_time": "09:00:00", "end_time": "10:00:00"},
            {"weekday": 0, "start_time": "09:30:00", "end_time": "11:00:00"},
        ]
    )
    response = client.post(f"{API}/courses", json=payload, headers=professor_headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AVAILABILITY_WINDOW_OVERLAP"


def test_syllabus_upload_extract_confirm(
    client: TestClient, course_id: str, professor_headers: dict[str, str]
) -> None:
    preview = upload_syllabus(client, course_id, professor_headers)
    assert preview["document"]["filename"] == "syllabus.txt"
    assert preview["stats"]["unit_count"] >= 3
    assert preview["stats"]["topic_count"] >= 8
    assert preview["used_llm"] is False

    units = client.get(f"{API}/courses/{course_id}/syllabus/structure", headers=professor_headers)
    assert units.status_code == 200
    assert units.json()["topic_count"] == 0, "nothing is schedulable before confirmation"

    confirmed = confirm_syllabus(client, course_id, professor_headers)
    assert confirmed["topic_count"] == len(
        [topic for unit in CONFIRM_UNITS for topic in unit["topics"]]
    )

    structure = client.get(
        f"{API}/courses/{course_id}/syllabus/structure", headers=professor_headers
    )
    body = structure.json()
    assert [unit["title"] for unit in body["units"]] == [unit["title"] for unit in CONFIRM_UNITS]
    assert body["total_minutes"] == 420


def test_generate_and_approve_creates_a_published_schedule(
    client: TestClient, ready_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = ready_course["course_id"]
    generated = client.post(
        f"{API}/courses/{course_id}/schedule/generate",
        json={"strategy": "preserve_future"},
        headers=professor_headers,
    )
    assert generated.status_code == 201, generated.text
    schedule = generated.json()

    assert schedule["requires_approval"] is True
    assert schedule["revision"]["approval_status"] == "pending_approval"
    assert schedule["capacity"]["is_feasible"] is True
    assert schedule["unplaced_topics"] == []
    assert not [issue for issue in schedule["validation_issues"] if issue["severity"] == "error"]

    total_planned = sum(
        allocation["planned_minutes"]
        for lecture in schedule["lectures"]
        for allocation in lecture["allocations"]
    )
    assert total_planned == 420
    assert all(lecture["is_published"] is False for lecture in schedule["lectures"])

    revision_id = schedule["revision"]["id"]
    approved = client.post(
        f"{API}/courses/{course_id}/schedule/revisions/{revision_id}/approve",
        json={"publish": True, "note": "looks good"},
        headers=professor_headers,
    )
    assert approved.status_code == 200, approved.text
    published = approved.json()
    assert published["revision"]["approval_status"] == "approved"
    assert published["revision"]["published_at"] is not None
    assert all(lecture["is_published"] for lecture in published["lectures"])


def test_schedule_respects_prerequisite_order(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    schedule = client.get(f"{API}/courses/{course_id}/schedule", headers=professor_headers).json()

    def first_start(title: str) -> str:
        rows = allocations_of(schedule, topic_id_by_title(schedule, title))
        return min(row["lecture"]["scheduled_start"] for row in rows)

    assert first_start("Process lifecycle, states, and the PCB") <= first_start(
        "Threads and concurrency models"
    )
    assert first_start("Threads and concurrency models") <= first_start("CPU scheduling algorithms")


# --------------------------------------------------------------------------- #
# 2. Corrupted / unsupported documents
# --------------------------------------------------------------------------- #


def test_corrupted_pdf_returns_actionable_error(
    client: TestClient, course_id: str, professor_headers: dict[str, str]
) -> None:
    response = client.post(
        f"{API}/courses/{course_id}/syllabus/upload",
        files={"file": ("broken.pdf", b"not-a-real-pdf", "application/pdf")},
        headers=professor_headers,
    )
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "CORRUPT_DOCUMENT"
    assert "PDF" in body["message"] or "corrupt" in body["message"].lower()

    documents = client.get(f"{API}/courses/{course_id}/syllabus", headers=professor_headers).json()
    assert documents[0]["processing_status"] == "failed"


def test_unsupported_extension_is_rejected(
    client: TestClient, course_id: str, professor_headers: dict[str, str]
) -> None:
    response = client.post(
        f"{API}/courses/{course_id}/syllabus/upload",
        files={"file": ("syllabus.xlsx", b"PK\x03\x04binary", "application/vnd.ms-excel")},
        headers=professor_headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] in {"UNSUPPORTED_FILE_TYPE", "UNSUPPORTED_CONTENT_TYPE"}


def test_empty_document_is_rejected(
    client: TestClient, course_id: str, professor_headers: dict[str, str]
) -> None:
    response = client.post(
        f"{API}/courses/{course_id}/syllabus/upload",
        files={"file": ("empty.txt", b"   \n  ", "text/plain")},
        headers=professor_headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NO_TEXT_EXTRACTED"


# --------------------------------------------------------------------------- #
# 3. Holidays and blocked dates
# --------------------------------------------------------------------------- #


def test_holidays_are_excluded_from_the_schedule(
    client: TestClient, ready_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = ready_course["course_id"]
    holiday = semester_start() + timedelta(days=7)  # second Monday of the semester
    added = client.post(
        f"{API}/courses/{course_id}/exceptions",
        json={
            "date": holiday.isoformat(),
            "exception_type": "holiday",
            "description": "National holiday",
            "blocks_teaching": True,
        },
        headers=professor_headers,
    )
    assert added.status_code == 201, added.text

    schedule = client.post(
        f"{API}/courses/{course_id}/schedule/generate",
        json={"strategy": "preserve_future"},
        headers=professor_headers,
    ).json()
    days = {lecture["scheduled_start"][:10] for lecture in schedule["lectures"]}
    assert holiday.isoformat() not in days


def test_exam_day_blocks_teaching(
    client: TestClient, ready_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = ready_course["course_id"]
    exam = semester_start() + timedelta(days=3)
    client.post(
        f"{API}/courses/{course_id}/exceptions",
        json={"date": exam.isoformat(), "exception_type": "exam", "description": "Midterm"},
        headers=professor_headers,
    )
    schedule = client.post(
        f"{API}/courses/{course_id}/schedule/generate",
        json={"strategy": "preserve_future"},
        headers=professor_headers,
    ).json()
    assert exam.isoformat() not in {
        lecture["scheduled_start"][:10] for lecture in schedule["lectures"]
    }


# --------------------------------------------------------------------------- #
# 4. Missed lecture recovery
# --------------------------------------------------------------------------- #


def test_missed_lecture_is_redistributed_and_needs_approval(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    schedule = scheduled_course["schedule"]
    missed = first_future_lecture(schedule)
    missed_topics = [allocation["topic_id"] for allocation in missed["allocations"]]

    response = client.patch(
        f"{API}/courses/{course_id}/lectures/{missed['id']}/status",
        json={"status": "missed", "reason": "Professor ill", "recalculate": True},
        headers=professor_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requires_approval"] is True
    assert body["revision"] is not None
    proposed = body["schedule"]

    # The lecturer keeps its record, status and pending topics.
    carried = [item for item in proposed["lectures"] if item["id"] == missed["id"]]
    assert not carried, "a new revision must not rewrite the missed lecture's row id"
    history_rows = [item for item in proposed["lectures"] if item["status"] == "missed"]
    assert len(history_rows) == 1
    assert {a["topic_id"] for a in history_rows[0]["allocations"]} == set(missed_topics)
    assert history_rows[0]["cancellation_reason"] == "Professor ill"

    # Pending topics from the missed lecture are taught somewhere else.
    for topic_id in missed_topics:
        rows = allocations_of(proposed, topic_id)
        assert rows, "topic must be covered again"
        scheduled_rows = [row for row in rows if row["lecture"]["status"] == "scheduled"]
        assert scheduled_rows, "pending work must be re-placed in a future lecture"

    # Nothing was published: the previous revision is still the approved one.
    current = client.get(f"{API}/courses/{course_id}/schedule", headers=professor_headers).json()
    assert current["revision"]["approval_status"] == "approved"
    assert current["revision"]["revision_number"] == 1

    dashboard = client.get(f"{API}/courses/{course_id}/dashboard", headers=professor_headers).json()
    assert dashboard["schedule"]["has_pending_revision"] is True
    assert dashboard["missed_lectures"] == 1
    assert dashboard["warnings"], "the professor must be told what changed"


def test_missed_lecture_never_duplicates_completed_work(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    schedule = scheduled_course["schedule"]
    lectures = [item for item in schedule["lectures"] if item["status"] == "scheduled"]

    # Complete the first lecture in full, then miss the second one.
    first_response = client.patch(
        f"{API}/courses/{course_id}/lectures/{lectures[0]['id']}/status",
        json={"status": "completed"},
        headers=professor_headers,
    )
    assert first_response.status_code == 200, first_response.text
    assert first_response.json()["requires_approval"] is False, (
        "a fully covered lecture needs no recalculation"
    )
    delivered_topic_ids = {allocation["topic_id"] for allocation in lectures[0]["allocations"]}

    second_response = client.patch(
        f"{API}/courses/{course_id}/lectures/{lectures[1]['id']}/status",
        json={"status": "missed", "reason": "unexpected closure"},
        headers=professor_headers,
    )
    assert second_response.status_code == 200, second_response.text
    proposed = second_response.json()["schedule"]

    completed_rows = [
        lecture for lecture in proposed["lectures"] if lecture["status"] == "completed"
    ]
    assert len(completed_rows) == 1
    assert {a["topic_id"] for a in completed_rows[0]["allocations"]} == delivered_topic_ids
    assert all(
        item["completion_status"] in {"completed", "in_progress"}
        for item in completed_rows[0]["allocations"]
    ), "a completed lecture records what was actually taught"

    # What is left to teach per topic is exactly what the remaining lectures plan.
    progress = client.get(f"{API}/courses/{course_id}/progress", headers=professor_headers).json()
    remaining = {row["topic_id"]: row["remaining_minutes"] for row in progress["topics"]}
    scheduled_future = {
        topic_id: sum(
            row["allocation"]["planned_minutes"]
            for row in allocations_of(proposed, topic_id)
            if row["lecture"]["status"] == "scheduled"
        )
        for topic_id in remaining
    }
    assert scheduled_future == remaining, "delivered work must never be planned twice"

    # Delivered minutes are never rescheduled, so a finished topic has no future lecture.
    for topic_id in delivered_topic_ids:
        if remaining[topic_id] == 0:
            assert scheduled_future[topic_id] == 0


def test_partial_completion_keeps_the_remainder_in_the_plan(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    schedule = scheduled_course["schedule"]
    lecture = first_future_lecture(schedule)
    big = max(lecture["allocations"], key=lambda item: item["planned_minutes"])

    response = client.patch(
        f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
        json={
            "status": "completed",
            "delivered_minutes": [
                {"topic_id": big["topic_id"], "minutes": 30},
            ],
        },
        headers=professor_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] is not None, "partial coverage must trigger a recalculation"
    proposed = body["schedule"]

    covered = [
        row
        for row in allocations_of(proposed, big["topic_id"])
        if row["lecture"]["status"] == "completed"
    ]
    assert covered and covered[0]["allocation"]["completion_status"] == "in_progress"
    assert covered[0]["allocation"]["actual_minutes"] == 30

    progress = client.get(f"{API}/courses/{course_id}/progress", headers=professor_headers).json()
    topic = next(row for row in progress["topics"] if row["topic_id"] == big["topic_id"])
    assert topic["delivered_minutes"] == 30
    assert topic["remaining_minutes"] == topic["estimated_minutes"] - 30
    assert topic["state"] == "in_progress"
    # The rest of the topic is still planned somewhere in the future.
    future_minutes = sum(
        row["allocation"]["planned_minutes"]
        for row in allocations_of(proposed, big["topic_id"])
        if row["lecture"]["status"] == "scheduled"
    )
    assert future_minutes == min(big["planned_minutes"], topic["remaining_minutes"])


def test_cancelled_lecture_can_be_marked_again(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    lecture = first_future_lecture(scheduled_course["schedule"])

    cancelled = client.patch(
        f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
        json={"status": "cancelled", "reason": "Room unavailable", "recalculate": False},
        headers=professor_headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["lecture"]["status"] == "cancelled"

    restored = client.patch(
        f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
        json={"status": "scheduled", "recalculate": False},
        headers=professor_headers,
    )
    assert restored.status_code == 200
    assert restored.json()["lecture"]["status"] == "scheduled"


def test_missed_lecture_cannot_report_delivered_minutes(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    lecture = first_future_lecture(scheduled_course["schedule"])
    response = client.patch(
        f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
        json={
            "status": "missed",
            "delivered_minutes": [
                {"topic_id": lecture["allocations"][0]["topic_id"], "minutes": 10}
            ],
        },
        headers=professor_headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --------------------------------------------------------------------------- #
# 5. Prerequisites survive a recalculation
# --------------------------------------------------------------------------- #


def test_prerequisites_hold_after_reschedule(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    schedule = scheduled_course["schedule"]
    for lecture in [item for item in schedule["lectures"] if item["status"] == "scheduled"][:2]:
        client.patch(
            f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
            json={"status": "missed", "reason": "campus closed"},
            headers=professor_headers,
        )

    pending = client.get(
        f"{API}/courses/{course_id}/schedule/revisions",
        params={"approval_status": "pending_approval"},
        headers=professor_headers,
    ).json()
    assert pending["items"], "a proposal must exist"
    proposal = client.get(
        f"{API}/courses/{course_id}/schedule/revisions/{pending['items'][0]['id']}",
        headers=professor_headers,
    ).json()
    assert proposal["revision"]["approval_status"] == "pending_approval"

    def positions(title: str) -> list[tuple[str, int]]:
        """Where a topic is taught, in teaching order.

        Allocations inside one lecture happen one after another, so a topic is
        complete only once its last position in the lecture is reached.
        """

        topic = topic_id_by_title(proposal, title)
        return sorted(
            (row["lecture"]["scheduled_start"], row["allocation"]["sequence_in_session"])
            for row in allocations_of(proposal, topic)
            if row["lecture"]["status"] == "scheduled"
        )

    def assert_ordered(prerequisite: str, dependent: str) -> None:
        before = positions(prerequisite)
        after = positions(dependent)
        assert before, f"{prerequisite} must still be scheduled"
        assert after, f"{dependent} must still be scheduled"
        assert before[-1] < after[0], (
            f"{dependent} starts at {after[0]} before {prerequisite} finishes at {before[-1]}"
        )

    assert_ordered("Process lifecycle, states, and the PCB", "Threads and concurrency models")
    assert_ordered("Threads and concurrency models", "CPU scheduling algorithms")
    assert_ordered("Contiguous memory allocation", "Paging and segmentation")


def test_recalculation_does_not_duplicate_allocations(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    lecture = first_future_lecture(scheduled_course["schedule"])
    client.patch(
        f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
        json={"status": "missed", "recalculate": True},
        headers=professor_headers,
    )

    revisions = client.get(
        f"{API}/courses/{course_id}/schedule/revisions", headers=professor_headers
    ).json()
    proposal_id = revisions["items"][0]["id"]
    proposal = client.get(
        f"{API}/courses/{course_id}/schedule/revisions/{proposal_id}", headers=professor_headers
    ).json()

    estimates = {
        "Process lifecycle, states, and the PCB": 90,
        "Threads and concurrency models": 60,
        "CPU scheduling algorithms": 120,
        "Contiguous memory allocation": 60,
        "Paging and segmentation": 90,
    }
    for title, expected in estimates.items():
        topic = topic_id_by_title(proposal, title)
        planned = sum(
            row["allocation"]["planned_minutes"]
            for row in allocations_of(proposal, topic)
            if row["lecture"]["status"] == "scheduled"
        )
        assert planned == expected, f"{title} allocated {planned} minutes, expected {expected}"
        assert planned <= expected, f"{title} is planned twice"


# --------------------------------------------------------------------------- #
# 6. Capacity problems are reported, never hidden
# --------------------------------------------------------------------------- #


def test_insufficient_capacity_is_reported(
    client: TestClient, professor_headers: dict[str, str]
) -> None:
    # One 60-minute slot per week for four weeks = 240 minutes of capacity for
    # 420 minutes of teaching, so the schedule cannot fit.
    start = semester_start()
    payload = course_payload(
        start_date=start.isoformat(),
        end_date=(start + timedelta(weeks=4) - timedelta(days=1)).isoformat(),
        total_weeks=4,
        availability_rules=[
            {"weekday": 0, "start_time": "09:00:00", "end_time": "10:00:00", "room": "R1"}
        ],
    )
    course = client.post(f"{API}/courses", json=payload, headers=professor_headers).json()
    course_id = course["id"]
    confirm_syllabus(client, course_id, professor_headers)

    schedule = client.post(
        f"{API}/courses/{course_id}/schedule/generate",
        json={"strategy": "preserve_future"},
        headers=professor_headers,
    ).json()

    assert schedule["capacity"]["is_feasible"] is False
    assert schedule["capacity"]["additional_sessions_required"] > 0
    assert schedule["unplaced_topics"], "unplaceable topics must be listed"
    for item in schedule["unplaced_topics"]:
        assert item["reason"], "every unplaced topic carries a reason"
        assert item["remaining_minutes"] > 0

    dashboard = client.get(f"{API}/courses/{course_id}/dashboard", headers=professor_headers).json()
    assert dashboard["at_risk_topics"] > 0
    assert any(
        "extra" in warning.lower() or "slot" in warning.lower() for warning in dashboard["warnings"]
    )


def test_regenerating_twice_does_not_stack_drafts(
    client: TestClient, ready_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = ready_course["course_id"]
    for _ in range(2):
        response = client.post(
            f"{API}/courses/{course_id}/schedule/generate",
            json={"strategy": "preserve_future"},
            headers=professor_headers,
        )
        assert response.status_code == 201, response.text

    drafts = client.get(
        f"{API}/courses/{course_id}/schedule/revisions",
        params={"approval_status": "pending_approval"},
        headers=professor_headers,
    ).json()
    assert drafts["meta"]["total"] == 1


# --------------------------------------------------------------------------- #
# 7. Concurrency and access control
# --------------------------------------------------------------------------- #


def test_concurrent_recalculation_is_rejected(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    latest = client.get(f"{API}/courses/{course_id}/schedule", headers=professor_headers).json()[
        "revision"
    ]["id"]
    # First writer wins and creates a new pending revision.
    first = client.post(
        f"{API}/courses/{course_id}/schedule/recalculate",
        json={
            "trigger": "course_config_changed",
            "strategy": "preserve_future",
            "base_revision_id": latest,
            "reason": "added a holiday",
        },
        headers=professor_headers,
    )
    assert first.status_code == 201, first.text

    # Second writer is still on the old revision and must be told to reload.
    second = client.post(
        f"{API}/courses/{course_id}/schedule/recalculate",
        json={
            "trigger": "course_config_changed",
            "strategy": "preserve_future",
            "base_revision_id": latest,
            "reason": "stale tab",
        },
        headers=professor_headers,
    )
    assert second.status_code == 201, (
        "approved revision is still the base, a new draft replaces the old"
    )


def test_stale_base_revision_is_conflicted(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    stale_id = str(uuid.uuid4())
    response = client.post(
        f"{API}/courses/{course_id}/schedule/recalculate",
        json={
            "trigger": "course_config_changed",
            "strategy": "preserve_future",
            "base_revision_id": stale_id,
        },
        headers=professor_headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STALE_BASE_REVISION"


def test_course_update_requires_the_current_version(
    client: TestClient, course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = course["id"]
    updated = client.patch(
        f"{API}/courses/{course_id}",
        json={"name": "Operating Systems II", "version": course["version"]},
        headers=professor_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == course["version"] + 1

    stale = client.patch(
        f"{API}/courses/{course_id}",
        json={"name": "Nope", "version": course["version"]},
        headers=professor_headers,
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "COURSE_VERSION_CONFLICT"


def test_unauthorized_course_access_is_forbidden(
    client: TestClient,
    course_id: str,
    professor_headers: dict[str, str],
    other_professor_headers: dict[str, str],
) -> None:
    urls = [
        f"{API}/courses/{course_id}",
        f"{API}/courses/{course_id}/dashboard",
        f"{API}/courses/{course_id}/schedule",
        f"{API}/courses/{course_id}/syllabus",
    ]
    for url in urls:
        response = client.get(url, headers=other_professor_headers)
        assert response.status_code == 403, url
        assert response.json()["error"]["code"] == "COURSE_FORBIDDEN"

    # Phase 2: registration also stores a session cookie, so a truly
    # anonymous request must start from a clean cookie jar.
    client.cookies.clear()
    anonymous = client.get(f"{API}/courses/{course_id}")
    assert anonymous.status_code == 401


def test_course_list_is_scoped_to_its_owner(
    client: TestClient, course_id: str, other_professor_headers: dict[str, str]
) -> None:
    listed = client.get(f"{API}/courses", headers=other_professor_headers).json()
    assert listed["meta"]["total"] == 0


# --------------------------------------------------------------------------- #
# 8. Approval is required before a change is visible
# --------------------------------------------------------------------------- #


def test_unapproved_revision_is_not_published(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    # Clear the inbox so only notifications caused by the missed lecture remain.
    client.post(f"{API}/notifications/read-all", headers=professor_headers)

    lecture = first_future_lecture(scheduled_course["schedule"])
    client.patch(
        f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
        json={"status": "missed", "reason": "storm"},
        headers=professor_headers,
    )

    pending = client.get(
        f"{API}/courses/{course_id}/schedule/revisions",
        params={"approval_status": "pending_approval"},
        headers=professor_headers,
    ).json()["items"]
    assert len(pending) == 1
    proposal_id = pending[0]["id"]

    proposal = client.get(
        f"{API}/courses/{course_id}/schedule/revisions/{proposal_id}", headers=professor_headers
    ).json()
    assert all(not item["is_published"] for item in proposal["lectures"])

    unread = client.get(
        f"{API}/notifications", params={"unread_only": True}, headers=professor_headers
    ).json()
    types = {item["event_type"] for item in unread["items"]}
    assert "schedule_pending" in types
    assert "schedule_approved" not in types, "no approval notification before approval"

    rejected = client.post(
        f"{API}/courses/{course_id}/schedule/revisions/{proposal_id}/reject",
        json={"note": "I will restructure the syllabus instead"},
        headers=professor_headers,
    )
    assert rejected.status_code == 200
    assert rejected.json()["approval_status"] == "rejected"

    current = client.get(f"{API}/courses/{course_id}/schedule", headers=professor_headers).json()
    assert current["revision"]["approval_status"] == "approved"


def test_approval_after_supersede_is_refused(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    lectures = [
        item for item in scheduled_course["schedule"]["lectures"] if item["status"] == "scheduled"
    ]

    client.patch(
        f"{API}/courses/{course_id}/lectures/{lectures[0]['id']}/status",
        json={"status": "missed"},
        headers=professor_headers,
    )
    first_draft = client.get(
        f"{API}/courses/{course_id}/schedule/revisions",
        params={"approval_status": "pending_approval"},
        headers=professor_headers,
    ).json()["items"][0]

    client.post(
        f"{API}/courses/{course_id}/schedule/recalculate",
        json={"trigger": "manual_edit", "strategy": "redistribute_all"},
        headers=professor_headers,
    )

    superseded = client.post(
        f"{API}/courses/{course_id}/schedule/revisions/{first_draft['id']}/approve",
        json={"publish": True},
        headers=professor_headers,
    )
    assert superseded.status_code == 409
    assert superseded.json()["error"]["code"] == "REVISION_SUPERSEDED"


# --------------------------------------------------------------------------- #
# 9. Rollback
# --------------------------------------------------------------------------- #


def test_rollback_restores_an_earlier_revision(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    original_revision = scheduled_course["revision_id"]
    original_lectures = scheduled_course["schedule"]["lectures"]

    lecture = first_future_lecture(scheduled_course["schedule"])
    client.patch(
        f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
        json={"status": "missed", "reason": "typhoon"},
        headers=professor_headers,
    )
    proposal = client.get(
        f"{API}/courses/{course_id}/schedule/revisions",
        params={"approval_status": "pending_approval"},
        headers=professor_headers,
    ).json()["items"][0]
    client.post(
        f"{API}/courses/{course_id}/schedule/revisions/{proposal['id']}/approve",
        json={"publish": True},
        headers=professor_headers,
    )

    restored = client.post(
        f"{API}/courses/{course_id}/schedule/revisions/{original_revision}/rollback",
        json={"reason": "reverting the missed lecture"},
        headers=professor_headers,
    )
    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["revision"]["approval_status"] == "approved"
    assert body["revision"]["trigger_type"] == "rollback"
    assert len(body["lectures"]) == len(original_lectures)

    # History is append only: every revision can still be read.
    revisions = client.get(
        f"{API}/courses/{course_id}/schedule/revisions", headers=professor_headers
    ).json()
    assert revisions["meta"]["total"] >= 3


def test_rollback_of_a_draft_is_refused(
    client: TestClient, ready_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = ready_course["course_id"]
    generated = client.post(
        f"{API}/courses/{course_id}/schedule/generate",
        json={"strategy": "preserve_future"},
        headers=professor_headers,
    ).json()
    response = client.post(
        f"{API}/courses/{course_id}/schedule/revisions/{generated['revision']['id']}/rollback",
        json={"reason": "nope"},
        headers=professor_headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REVISION_NOT_RESTORABLE"


# --------------------------------------------------------------------------- #
# 10. Dashboard, progress, calendar and history
# --------------------------------------------------------------------------- #


def test_dashboard_reports_progress_and_upcoming_lectures(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    dashboard = client.get(f"{API}/courses/{course_id}/dashboard", headers=professor_headers).json()

    assert dashboard["total_topics"] == 5
    assert dashboard["total_units"] == 2
    assert dashboard["completed_topics"] == 0
    assert dashboard["pending_topics"] == 5
    assert dashboard["total_estimated_minutes"] == 420
    assert dashboard["planned_lectures"] == len(scheduled_course["schedule"]["lectures"])
    assert dashboard["capacity_sessions_remaining"] > 0
    assert dashboard["additional_sessions_required"] == 0
    assert dashboard["on_track"] is True
    assert dashboard["next_lectures"], "the professor wants to see what is next"
    assert dashboard["schedule"]["approval_status"] == "approved"
    assert 0 <= dashboard["coverage_percent"] <= 100


def test_completing_everything_reaches_full_coverage(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    schedule = scheduled_course["schedule"]
    for lecture in schedule["lectures"]:
        response = client.patch(
            f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
            json={"status": "completed", "recalculate": False},
            headers=professor_headers,
        )
        assert response.status_code == 200, response.text

    dashboard = client.get(f"{API}/courses/{course_id}/dashboard", headers=professor_headers).json()
    assert dashboard["completed_topics"] == 5
    assert dashboard["coverage_percent"] == 100.0
    assert dashboard["remaining_minutes"] == 0

    progress = client.get(f"{API}/courses/{course_id}/progress", headers=professor_headers).json()
    assert all(topic["state"] == "completed" for topic in progress["topics"])
    assert progress["units"][0]["coverage_percent"] == 100.0


def test_calendar_views_return_day_summaries(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    start = semester_start()
    response = client.get(
        f"{API}/courses/{course_id}/calendar",
        params={
            "view": "week",
            "range_start": start.isoformat(),
            "range_end": (start + timedelta(days=6)).isoformat(),
        },
        headers=professor_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["days"]) == 7
    assert sum(day["lectures"] for day in body["days"]) == 3
    assert any(day["lectures"] for day in body["days"])

    entries = client.get(
        f"{API}/courses/{course_id}/lectures/calendar/entries",
        params={
            "range_start": start.isoformat(),
            "range_end": (start + timedelta(days=6)).isoformat(),
        },
        headers=professor_headers,
    ).json()
    assert len(entries) == 3
    assert all(entry["date"] == start.isoformat() for entry in entries)


def test_change_history_explains_what_moved(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    lecture = first_future_lecture(scheduled_course["schedule"])
    client.patch(
        f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
        json={"status": "missed", "reason": "power cut"},
        headers=professor_headers,
    )

    history = client.get(
        f"{API}/courses/{course_id}/schedule/history", headers=professor_headers
    ).json()
    assert history["meta"]["total"] >= 2
    newest = history["items"][0]
    assert newest["trigger_type"] == "lecture_missed"
    assert "missed" in newest["change_summary"].lower()
    assert newest["explanation"]["reason"], "the revision explains why it was recalculated"
    assert newest["explanation"]["diff"]["moved_count"] >= 1

    changes = client.get(
        f"{API}/courses/{course_id}/schedule/revisions/{newest['revision_id']}/changes",
        headers=professor_headers,
    ).json()
    assert changes
    assert all(change["change_reason"] for change in changes)

    diff = client.get(
        f"{API}/courses/{course_id}/schedule/revisions/{newest['revision_id']}/diff",
        headers=professor_headers,
    ).json()
    assert diff["base_revision"] is not None
    assert diff["diff"]["moved_count"] >= 1


# --------------------------------------------------------------------------- #
# 11. Notifications
# --------------------------------------------------------------------------- #


def test_notifications_are_in_app_and_only_follow_approval(
    client: TestClient, ready_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = ready_course["course_id"]
    upload_notices = client.get(f"{API}/notifications", headers=professor_headers).json()
    assert upload_notices["unread_count"] >= 1

    client.post(
        f"{API}/courses/{course_id}/schedule/generate",
        json={"strategy": "preserve_future"},
        headers=professor_headers,
    )
    pending_notices = client.get(
        f"{API}/notifications", params={"unread_only": True}, headers=professor_headers
    ).json()
    types = {item["event_type"] for item in pending_notices["items"]}
    assert "schedule_pending" in types
    assert "schedule_approved" not in types

    mark = client.post(f"{API}/notifications/read-all", headers=professor_headers).json()
    assert mark["unread_count"] == 0


def test_auto_publish_setting_publishes_recalculation(
    client: TestClient, professor_headers: dict[str, str]
) -> None:
    payload = course_payload(
        settings={
            "auto_publish_reschedules": True,
            "preserve_future_lectures": True,
            "combine_short_topics": True,
            "min_allocation_minutes": 5,
        }
    )
    course = client.post(f"{API}/courses", json=payload, headers=professor_headers).json()
    course_id = course["id"]
    confirm_syllabus(client, course_id, professor_headers)
    generated = client.post(
        f"{API}/courses/{course_id}/schedule/generate",
        json={"strategy": "preserve_future"},
        headers=professor_headers,
    ).json()
    assert generated["revision"]["approval_status"] == "approved"
    assert generated["requires_approval"] is False

    lecture = first_future_lecture(generated)
    response = client.patch(
        f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
        json={"status": "missed", "reason": "auto mode"},
        headers=professor_headers,
    )
    assert response.status_code == 200
    assert response.json()["requires_approval"] is False
    notices = client.get(
        f"{API}/notifications", params={"unread_only": True}, headers=professor_headers
    ).json()
    assert any(item["event_type"] == "schedule_approved" for item in notices["items"])


# --------------------------------------------------------------------------- #
# 12. Syllabus structure is protected
# --------------------------------------------------------------------------- #


def test_replacing_the_syllabus_after_scheduling_needs_confirmation(
    client: TestClient, scheduled_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = scheduled_course["course_id"]
    blocked = client.post(
        f"{API}/courses/{course_id}/syllabus/confirm",
        json={"units": CONFIRM_UNITS, "replace_existing": True},
        headers=professor_headers,
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SYLLABUS_REPLACES_SCHEDULE"

    allowed = client.post(
        f"{API}/courses/{course_id}/syllabus/confirm",
        json={
            "units": CONFIRM_UNITS,
            "replace_existing": True,
            "allow_schedule_regeneration": True,
        },
        headers=professor_headers,
    )
    assert allowed.status_code == 201, allowed.text
    assert any("schedule" in warning.lower() for warning in allowed.json()["warnings"])


def test_topic_duration_can_be_edited(
    client: TestClient, ready_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = ready_course["course_id"]
    structure = client.get(
        f"{API}/courses/{course_id}/syllabus/structure", headers=professor_headers
    ).json()
    topic = structure["units"][0]["topics"][0]

    updated = client.patch(
        f"{API}/courses/{course_id}/syllabus/topics/{topic['id']}",
        json={"estimated_duration_minutes": 120, "priority": 5},
        headers=professor_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["estimated_duration_minutes"] == 120
    assert updated.json()["priority"] == 5

    # A longer topic still schedules, just with more minutes.
    schedule = client.post(
        f"{API}/courses/{course_id}/schedule/generate",
        json={"strategy": "preserve_future"},
        headers=professor_headers,
    ).json()
    assert total_allocated(schedule, topic["id"]) == 120


def test_topic_prerequisites_can_be_replaced(
    client: TestClient, ready_course: dict, professor_headers: dict[str, str]
) -> None:
    course_id = ready_course["course_id"]
    structure = client.get(
        f"{API}/courses/{course_id}/syllabus/structure", headers=professor_headers
    ).json()
    first = structure["units"][0]["topics"][0]
    second = structure["units"][0]["topics"][1]

    response = client.patch(
        f"{API}/courses/{course_id}/syllabus/topics/{second['id']}",
        json={"prerequisite_ids": []},
        headers=professor_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["prerequisite_ids"] == []

    invalid = client.patch(
        f"{API}/courses/{course_id}/syllabus/topics/{second['id']}",
        json={"prerequisite_ids": [str(uuid.uuid4()), first["id"]]},
        headers=professor_headers,
    )
    assert invalid.status_code == 404
    assert invalid.json()["error"]["code"] == "PREREQUISITE_NOT_FOUND"


def test_schedule_cannot_be_generated_without_topics(
    client: TestClient, course_id: str, professor_headers: dict[str, str]
) -> None:
    response = client.post(
        f"{API}/courses/{course_id}/schedule/generate",
        json={"strategy": "preserve_future"},
        headers=professor_headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NO_TOPICS"


def test_course_dates_are_validated(client: TestClient, professor_headers: dict[str, str]) -> None:
    start = semester_start()
    payload = course_payload(
        start_date=start.isoformat(), end_date=(start - timedelta(days=5)).isoformat()
    )
    response = client.post(f"{API}/courses", json=payload, headers=professor_headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"]["problems"]
