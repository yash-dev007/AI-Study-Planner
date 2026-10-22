"""Regression tests for defects fixed while integrating the audit teams.

These cover cross-cutting issues that did not belong to a single team's file
scope: course timezone validation, the ignored ``auto_publish`` flag, per-teacher
AI opt-in, bounded refine payloads, order-independent prerequisite validation,
and bounded upload reads.
"""

from __future__ import annotations

import pytest
from app.api.routes import syllabus as syllabus_routes
from app.core.config import settings
from app.schemas.schedule import GenerateScheduleRequest
from app.schemas.syllabus import SyllabusConfirmRequest, SyllabusPromptRefineRequest
from app.services import syllabus as syllabus_service
from fastapi.testclient import TestClient
from pydantic import ValidationError
from tests.conftest import MockLLMInterpreter, course_payload

API = "/api/v1"


def _topic(key: str, *, prerequisites: list[str] | None = None) -> dict[str, object]:
    return {
        "key": key,
        "title": f"Topic {key}",
        "description": "",
        "learning_objectives": [],
        "estimated_duration_minutes": 60,
        "difficulty": "medium",
        "confidence": 0.8,
        "needs_review": False,
        "prerequisite_keys": prerequisites or [],
        "sequence_number": 1,
    }


def _unit(key: str, *, sequence: int, topics: list[dict[str, object]]) -> dict[str, object]:
    return {
        "key": key,
        "title": f"Unit {key}",
        "description": "",
        "sequence_number": sequence,
        "topics": topics,
    }


# --------------------------------------------------------------------------- #
# Course timezone must be a real IANA zone (calendar generation depends on it)
# --------------------------------------------------------------------------- #


def test_course_create_rejects_an_unknown_timezone(
    client: TestClient, professor_headers: dict[str, str]
) -> None:
    response = client.post(
        f"{API}/courses",
        json=course_payload(timezone="Mars/Olympus"),
        headers=professor_headers,
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_course_update_rejects_an_unknown_timezone(
    client: TestClient, course: dict[str, object], professor_headers: dict[str, str]
) -> None:
    response = client.patch(
        f"{API}/courses/{course['id']}",
        json={"timezone": "Mars/Olympus"},
        headers=professor_headers,
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_course_accepts_a_real_iana_timezone(
    client: TestClient, professor_headers: dict[str, str]
) -> None:
    response = client.post(
        f"{API}/courses",
        json=course_payload(name="Local time course", timezone="America/New_York"),
        headers=professor_headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["timezone"] == "America/New_York"


# --------------------------------------------------------------------------- #
# Generating must never silently publish a revision
# --------------------------------------------------------------------------- #


def test_generate_rejects_the_removed_auto_publish_flag(
    client: TestClient, ready_course: dict[str, object], professor_headers: dict[str, str]
) -> None:
    response = client.post(
        f"{API}/courses/{ready_course['course_id']}/schedule/generate",
        json={"strategy": "preserve_future", "auto_publish": True},
        headers=professor_headers,
    )
    assert response.status_code == 422, response.text
    assert "auto_publish" in response.text


def test_generate_request_has_no_auto_publish_field() -> None:
    assert "auto_publish" not in GenerateScheduleRequest.model_fields


# --------------------------------------------------------------------------- #
# I8: AI is opt-in per teacher, even when the server has a provider key
# --------------------------------------------------------------------------- #


def test_syllabus_ai_requires_the_teachers_opt_in(
    client: TestClient,
    ready_course: dict[str, object],
    professor_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = MockLLMInterpreter()
    monkeypatch.setattr(syllabus_service, "build_llm_interpreter", lambda _settings: spy)
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-v1-audit-test")
    monkeypatch.setattr(settings, "openrouter_model", "google/gemini-2.0-flash-001")

    course_id = ready_course["course_id"]
    text = "Unit 1: Foundations\nTopic 1: Introduction (60 min)\n"

    # The server can call a provider, but this teacher never enabled AI.
    disabled = client.post(
        f"{API}/courses/{course_id}/syllabus/text",
        json={"title": "Opt-out", "text": text},
        headers=professor_headers,
    )
    assert disabled.status_code == 201, disabled.text
    assert spy.calls == []
    assert disabled.json()["used_llm"] is False

    enabled = client.put(
        f"{API}/settings",
        json={"ai_enabled": True, "preferred_ai_provider": "openrouter"},
        headers=professor_headers,
    )
    assert enabled.status_code == 200, enabled.text

    opted_in = client.post(
        f"{API}/courses/{course_id}/syllabus/text",
        json={"title": "Opt-in", "text": text},
        headers=professor_headers,
    )
    assert opted_in.status_code == 201, opted_in.text
    assert spy.calls != []
    assert opted_in.json()["used_llm"] is True


def test_production_refuses_ui_ai_key_writes(
    client: TestClient,
    professor_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A teacher must not be able to replace the server-wide key in production."""

    monkeypatch.setattr(settings, "app_env", "production")
    response = client.post(
        f"{API}/settings/ai-config",
        json={
            "provider": "openrouter",
            "api_key": "sk-or-v1-attacker",
            "model": "google/gemini-2.0-flash-001",
        },
        headers=professor_headers,
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "AI_CONFIG_UI_DISABLED"

    # The UI can still discover that keys are server-managed and enable AI for
    # this teacher without ever handling the provider secret.
    settings_response = client.get(f"{API}/settings", headers=professor_headers)
    assert settings_response.status_code == 200, settings_response.text
    assert settings_response.json()["ai_config_writable"] is False

    enabled = client.put(
        f"{API}/settings",
        json={"ai_enabled": True, "preferred_ai_provider": "openrouter"},
        headers=professor_headers,
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["ai_enabled"] is True
    assert enabled.json()["preferred_ai_provider"] == "openrouter"


# --------------------------------------------------------------------------- #
# Refine payload is bounded, and prerequisite validation is order-independent
# --------------------------------------------------------------------------- #


def test_refine_current_units_is_bounded() -> None:
    units = [
        _unit(f"u{index}", sequence=index + 1, topics=[_topic(f"t{index}")])
        for index in range(101)
    ]
    with pytest.raises(ValidationError):
        SyllabusPromptRefineRequest(prompt="Split the first unit", current_units=units)  # type: ignore[arg-type]


def test_prerequisite_may_point_at_a_later_unit() -> None:
    units = [
        _unit("u1", sequence=1, topics=[_topic("t1", prerequisites=["t2"])]),
        _unit("u2", sequence=2, topics=[_topic("t2")]),
    ]
    request = SyllabusConfirmRequest(units=units)  # type: ignore[arg-type]
    assert len(request.units) == 2


def test_unknown_prerequisite_is_still_rejected() -> None:
    units = [_unit("u1", sequence=1, topics=[_topic("t1", prerequisites=["missing"])])]
    with pytest.raises(ValidationError):
        SyllabusConfirmRequest(units=units)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Oversized uploads are rejected while reading, not after buffering
# --------------------------------------------------------------------------- #


def test_oversized_upload_is_rejected_before_buffering(
    client: TestClient,
    ready_course: dict[str, object],
    professor_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(syllabus_routes, "MAX_UPLOAD_BYTES", 16)
    response = client.post(
        f"{API}/courses/{ready_course['course_id']}/syllabus/upload",
        files={"file": ("big.txt", b"x" * 64, "text/plain")},
        headers=professor_headers,
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert response.json()["error"]["details"]["size_bytes"] > 16
