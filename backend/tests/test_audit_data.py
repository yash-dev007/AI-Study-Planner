"""Regression tests for the DATA audit of the syllabus feature.

Each test pins one defect found during the review:

* a syllabus replacement deleting topics that carry recorded lecture history,
* negative sequence numbers leaking out of a partial structure save,
* the topic-order PATCH colliding with ``uq_topics_unit_sequence``,
* an explicit ``null`` reaching a NOT NULL column and surfacing as a 409,
* ownership checks on syllabus ids, archived rows in scheduling, and course
  date validation.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi.testclient import TestClient
from tests.conftest import CONFIRM_UNITS, course_payload

API = "/api/v1"


def _structure(client: TestClient, course_id: str, headers: dict[str, str]) -> dict[str, Any]:
    response = client.get(f"{API}/courses/{course_id}/syllabus/structure", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _complete_one_lecture(
    client: TestClient, scheduled_course: dict[str, Any], headers: dict[str, str]
) -> dict[str, Any]:
    """Complete the first planned lecture and return its pre-completion row."""

    course_id = scheduled_course["course_id"]
    lecture = next(
        item for item in scheduled_course["schedule"]["lectures"] if item["status"] == "scheduled"
    )
    response = client.patch(
        f"{API}/courses/{course_id}/lectures/{lecture['id']}/status",
        json={"status": "completed"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return lecture


def _topic_ids(structure: dict[str, Any]) -> set[str]:
    return {topic["id"] for unit in structure["units"] for topic in unit["topics"]}


# --------------------------------------------------------------------------- #
# I4: recorded history is immutable
# --------------------------------------------------------------------------- #


def test_confirm_replace_refuses_to_destroy_recorded_history(
    client: TestClient,
    scheduled_course: dict[str, Any],
    professor_headers: dict[str, str],
) -> None:
    """Replacing the syllabus must never wipe delivered minutes (I4)."""

    course_id = scheduled_course["course_id"]
    lecture = _complete_one_lecture(client, scheduled_course, professor_headers)
    completed_topic_ids = {item["topic_id"] for item in lecture["allocations"]}

    before = client.get(f"{API}/courses/{course_id}/progress", headers=professor_headers)
    assert before.status_code == 200, before.text
    delivered_before = sum(row["delivered_minutes"] for row in before.json()["topics"])
    assert delivered_before > 0

    replaced = client.post(
        f"{API}/courses/{course_id}/syllabus/confirm",
        json={
            "units": CONFIRM_UNITS,
            "replace_existing": True,
            "allow_schedule_regeneration": True,
        },
        headers=professor_headers,
    )
    assert replaced.status_code == 409, replaced.text
    body = replaced.json()["error"]
    assert body["code"] == "SYLLABUS_HAS_HISTORY"
    assert body["details"]["closed_lecture_count"] >= 1
    assert body["details"]["can_archive"] is True

    # Nothing was deleted, and the delivered minutes are still there.
    after = client.get(f"{API}/courses/{course_id}/progress", headers=professor_headers)
    assert after.status_code == 200, after.text
    assert sum(row["delivered_minutes"] for row in after.json()["topics"]) == delivered_before
    assert completed_topic_ids <= _topic_ids(_structure(client, course_id, professor_headers))

    history = client.get(f"{API}/courses/{course_id}/schedule", headers=professor_headers)
    assert history.status_code == 200, history.text
    completed = [row for row in history.json()["lectures"] if row["status"] == "completed"]
    assert len(completed) == 1
    assert {item["topic_id"] for item in completed[0]["allocations"]} == completed_topic_ids


def test_delete_is_blocked_when_recorded_history_exists(
    client: TestClient,
    scheduled_course: dict[str, Any],
    professor_headers: dict[str, str],
) -> None:
    """Deleting the topic (or its unit) must be refused while history exists."""

    course_id = scheduled_course["course_id"]
    lecture = _complete_one_lecture(client, scheduled_course, professor_headers)
    topic_id = lecture["allocations"][0]["topic_id"]
    structure = _structure(client, course_id, professor_headers)
    unit_id = next(
        unit["id"]
        for unit in structure["units"]
        if any(topic["id"] == topic_id for topic in unit["topics"])
    )

    topic_response = client.delete(
        f"{API}/courses/{course_id}/syllabus/topics/{topic_id}",
        headers=professor_headers,
    )
    assert topic_response.status_code == 409, topic_response.text
    assert topic_response.json()["error"]["code"] == "TOPIC_HAS_HISTORY"

    unit_response = client.delete(
        f"{API}/courses/{course_id}/syllabus/units/{unit_id}",
        headers=professor_headers,
    )
    assert unit_response.status_code == 409, unit_response.text
    assert unit_response.json()["error"]["code"] == "UNIT_HAS_HISTORY"


# --------------------------------------------------------------------------- #
# Structure save: omitted rows keep a valid, non-negative order
# --------------------------------------------------------------------------- #


def test_partial_structure_save_keeps_omitted_rows_after_saved_order(
    client: TestClient, ready_course: dict[str, Any], professor_headers: dict[str, str]
) -> None:
    """A partial PUT must not park omitted rows on negative sequence numbers."""

    course_id = ready_course["course_id"]
    before = _structure(client, course_id, professor_headers)
    unit_one, unit_two = before["units"]
    kept_topic = unit_one["topics"][0]

    saved = client.put(
        f"{API}/courses/{course_id}/syllabus",
        json={
            "units": [
                {
                    "id": unit_one["id"],
                    "title": unit_one["title"],
                    "description": unit_one["description"],
                    "topics": [
                        {
                            "id": kept_topic["id"],
                            "title": kept_topic["title"],
                            "description": kept_topic["description"],
                            "estimated_duration_minutes": kept_topic["estimated_duration_minutes"],
                            "difficulty": kept_topic["difficulty"],
                            "priority": kept_topic["priority"],
                            "is_splittable": kept_topic["is_splittable"],
                            "needs_review": kept_topic["needs_review"],
                        }
                    ],
                }
            ]
        },
        headers=professor_headers,
    )
    assert saved.status_code == 200, saved.text

    after = _structure(client, course_id, professor_headers)
    unit_one_after, unit_two_after = after["units"]

    # The saved unit comes first, the omitted one follows it.
    assert [unit["sequence_number"] for unit in after["units"]] == [1, 2]
    assert [unit["id"] for unit in after["units"]] == [unit_one["id"], unit_two["id"]]

    # No row was destroyed and every row has a positive, contiguous order.
    for unit in after["units"]:
        sequences = sorted(topic["sequence_number"] for topic in unit["topics"])
        assert sequences == list(range(1, len(sequences) + 1))

    # Relative order inside each unit is preserved.
    assert [topic["title"] for topic in unit_one_after["topics"]] == [
        topic["title"] for topic in before["units"][0]["topics"]
    ]
    assert [topic["title"] for topic in unit_two_after["topics"]] == [
        topic["title"] for topic in before["units"][1]["topics"]
    ]
    assert after["topic_count"] == before["topic_count"]


# --------------------------------------------------------------------------- #
# Topic order PATCH
# --------------------------------------------------------------------------- #


def test_topic_sequence_patch_moves_the_topic_without_a_constraint_error(
    client: TestClient, ready_course: dict[str, Any], professor_headers: dict[str, str]
) -> None:
    """Moving a topic to an occupied position must reorder, not fail with 409."""

    course_id = ready_course["course_id"]
    structure = _structure(client, course_id, professor_headers)
    first, second, third = structure["units"][0]["topics"]

    response = client.patch(
        f"{API}/courses/{course_id}/syllabus/topics/{third['id']}",
        json={"sequence_number": first["sequence_number"]},
        headers=professor_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["sequence_number"] == 1

    after = _structure(client, course_id, professor_headers)
    topics = after["units"][0]["topics"]
    assert [topic["id"] for topic in topics] == [third["id"], first["id"], second["id"]]
    assert [topic["sequence_number"] for topic in topics] == [1, 2, 3]


# --------------------------------------------------------------------------- #
# Explicit null is a validation error, not a constraint violation
# --------------------------------------------------------------------------- #


def test_explicit_null_on_not_null_fields_is_a_validation_error(
    client: TestClient, ready_course: dict[str, Any], professor_headers: dict[str, str]
) -> None:
    course_id = ready_course["course_id"]
    structure = _structure(client, course_id, professor_headers)
    unit = structure["units"][0]
    topic = unit["topics"][0]

    unit_response = client.patch(
        f"{API}/courses/{course_id}/syllabus/units/{unit['id']}",
        json={"title": None},
        headers=professor_headers,
    )
    assert unit_response.status_code == 422, unit_response.text
    assert unit_response.json()["error"]["code"] == "VALIDATION_ERROR"

    topic_response = client.patch(
        f"{API}/courses/{course_id}/syllabus/topics/{topic['id']}",
        json={"description": None},
        headers=professor_headers,
    )
    assert topic_response.status_code == 422, topic_response.text
    assert topic_response.json()["error"]["code"] == "VALIDATION_ERROR"

    course_response = client.patch(
        f"{API}/courses/{course_id}",
        json={"name": None},
        headers=professor_headers,
    )
    assert course_response.status_code == 422, course_response.text
    assert course_response.json()["error"]["code"] == "VALIDATION_ERROR"


# --------------------------------------------------------------------------- #
# Ownership and scheduling scope
# --------------------------------------------------------------------------- #


def test_another_professor_cannot_read_the_syllabus(
    client: TestClient,
    ready_course: dict[str, Any],
    other_professor_headers: dict[str, str],
) -> None:
    """I7: ownership failures are indistinguishable from a missing course."""

    response = client.get(
        f"{API}/courses/{ready_course['course_id']}/syllabus/structure",
        headers=other_professor_headers,
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "COURSE_NOT_FOUND"


def test_syllabus_ids_from_another_course_return_404(
    client: TestClient, ready_course: dict[str, Any], professor_headers: dict[str, str]
) -> None:
    source_id = ready_course["course_id"]
    source = _structure(client, source_id, professor_headers)
    unit = source["units"][0]
    topic = unit["topics"][0]

    other = client.post(
        f"{API}/courses",
        json=course_payload(name="Data Structures"),
        headers=professor_headers,
    )
    assert other.status_code == 201, other.text
    other_id = other.json()["id"]

    unit_response = client.patch(
        f"{API}/courses/{other_id}/syllabus/units/{unit['id']}",
        json={"title": "Moved unit"},
        headers=professor_headers,
    )
    assert unit_response.status_code == 404, unit_response.text
    assert unit_response.json()["error"]["code"] == "UNIT_NOT_FOUND"

    topic_response = client.patch(
        f"{API}/courses/{other_id}/syllabus/topics/{topic['id']}",
        json={"title": "Moved topic"},
        headers=professor_headers,
    )
    assert topic_response.status_code == 404, topic_response.text
    assert topic_response.json()["error"]["code"] == "TOPIC_NOT_FOUND"


def test_archived_unit_is_excluded_from_new_schedules(
    client: TestClient, ready_course: dict[str, Any], professor_headers: dict[str, str]
) -> None:
    course_id = ready_course["course_id"]
    structure = _structure(client, course_id, professor_headers)
    archived_unit = structure["units"][1]
    archived_topic_ids = {topic["id"] for topic in archived_unit["topics"]}

    archive = client.patch(
        f"{API}/courses/{course_id}/syllabus/units/{archived_unit['id']}",
        json={"archived": True},
        headers=professor_headers,
    )
    assert archive.status_code == 200, archive.text

    after = _structure(client, course_id, professor_headers)
    assert after["topic_count"] == 3
    assert after["total_minutes"] == 90 + 60 + 120

    generated = client.post(
        f"{API}/courses/{course_id}/schedule/generate",
        json={"strategy": "preserve_future"},
        headers=professor_headers,
    )
    assert generated.status_code == 201, generated.text
    scheduled_topic_ids = {
        allocation["topic_id"]
        for lecture in generated.json()["lectures"]
        for allocation in lecture["allocations"]
    }
    assert scheduled_topic_ids.isdisjoint(archived_topic_ids)


def test_structure_reports_prerequisites_from_the_bulk_lookup(
    client: TestClient, ready_course: dict[str, Any], professor_headers: dict[str, str]
) -> None:
    structure = _structure(client, ready_course["course_id"], professor_headers)
    topics = {topic["title"]: topic for unit in structure["units"] for topic in unit["topics"]}
    assert topics["Threads and concurrency models"]["prerequisite_ids"] == [
        topics["Process lifecycle, states, and the PCB"]["id"]
    ]


# --------------------------------------------------------------------------- #
# Course date validation
# --------------------------------------------------------------------------- #


def test_course_update_rejects_target_completion_outside_the_semester(
    client: TestClient, course: dict[str, Any], professor_headers: dict[str, str]
) -> None:
    end_date = date.fromisoformat(course["end_date"])
    response = client.patch(
        f"{API}/courses/{course['id']}",
        json={"target_completion_date": (end_date + timedelta(days=1)).isoformat()},
        headers=professor_headers,
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "INVALID_TARGET_DATE"
