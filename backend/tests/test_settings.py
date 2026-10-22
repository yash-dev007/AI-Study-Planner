"""Phase 2: per-teacher settings, safe configuration reporting, isolation."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.core.config import settings
from fastapi.testclient import TestClient

API = "/api/v1"
PASSWORD = "ProfPassw0rd!"


def _register(client: TestClient, *, name: str = "Professor") -> dict[str, str]:
    response = client.post(
        f"{API}/auth/register",
        json={
            "name": name,
            "email": f"settings-{uuid.uuid4().hex[:8]}@example.edu",
            "password": PASSWORD,
            "role": "professor",
        },
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _get(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.get(f"{API}/settings", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_settings_defaults_match_the_service_configuration(
    client: TestClient, professor_headers: dict[str, str]
) -> None:
    body = _get(client, professor_headers)
    assert body["default_lecture_minutes"] == settings.default_topic_minutes
    assert body["default_teaching_days"] == []
    assert body["weekly_email_enabled"] is False
    assert body["email_weekday"] == 0
    assert body["email_hour_local"] == 8
    assert body["ai_enabled"] is False
    assert body["preferred_ai_provider"] == "none"
    assert body["preferred_ai_model"] == ""
    assert body["ai_config_writable"] is True
    assert body["internet_required"] is False
    assert body["openrouter_configured"] == bool(settings.openrouter_api_key)
    assert body["smtp_configured"] == settings.smtp_configured


def test_settings_update_persists_and_normalizes_weekdays(
    client: TestClient, professor_headers: dict[str, str]
) -> None:
    updated = client.put(
        f"{API}/settings",
        json={
            "default_lecture_minutes": 90,
            "default_teaching_days": [4, 0, 4, 2],
            "weekly_email_enabled": True,
            "email_weekday": 2,
            "email_hour_local": 7,
            "ai_enabled": True,
            "preferred_ai_provider": "ollama",
            "preferred_ai_model": "llama3.1:8b",
        },
        headers=professor_headers,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["default_lecture_minutes"] == 90
    assert body["default_teaching_days"] == [0, 2, 4]
    assert body["weekly_email_enabled"] is True
    assert body["preferred_ai_provider"] == "ollama"

    assert _get(client, professor_headers) == body


def test_settings_patch_leaves_unseen_fields_alone(
    client: TestClient, professor_headers: dict[str, str]
) -> None:
    client.put(
        f"{API}/settings",
        json={"weekly_email_enabled": True, "email_hour_local": 6},
        headers=professor_headers,
    )
    response = client.put(
        f"{API}/settings", json={"default_lecture_minutes": 75}, headers=professor_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["default_lecture_minutes"] == 75
    assert body["weekly_email_enabled"] is True
    assert body["email_hour_local"] == 6


@pytest.mark.parametrize(
    "payload",
    [
        {"default_lecture_minutes": 5},
        {"default_lecture_minutes": 999},
        {"default_teaching_days": [0, 7]},
        {"email_weekday": 9},
        {"email_hour_local": 24},
        {"preferred_ai_provider": "openai"},
        {"preferred_ai_model": "x" * 121},
    ],
    ids=[
        "too-short-lecture",
        "too-long-lecture",
        "invalid-weekday",
        "weekday-out-of-range",
        "hour-out-of-range",
        "unknown-provider",
        "model-name-too-long",
    ],
)
def test_settings_validation_rejects_impossible_values(
    client: TestClient, professor_headers: dict[str, str], payload: dict[str, Any]
) -> None:
    response = client.put(f"{API}/settings", json=payload, headers=professor_headers)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_settings_never_return_configured_secrets(
    client: TestClient, professor_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-v1-supersecret-value")
    monkeypatch.setattr(settings, "smtp_password", "smtp-supersecret-value")
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.edu")
    monkeypatch.setattr(settings, "smtp_from", "professor@example.edu")

    read = _get(client, professor_headers)
    assert read["openrouter_configured"] is True
    assert read["smtp_configured"] is True

    updated = client.put(
        f"{API}/settings",
        json={"ai_enabled": True, "preferred_ai_provider": "openrouter"},
        headers=professor_headers,
    )
    assert updated.status_code == 200
    for response in (read, updated.json()):
        serialized = str(response)
        assert "supersecret" not in serialized
        assert "api_key" not in response
        assert "password" not in response
        assert "smtp_host" not in response


def test_internet_requirement_follows_the_provider(
    client: TestClient, professor_headers: dict[str, str]
) -> None:
    online = client.put(
        f"{API}/settings",
        json={"ai_enabled": True, "preferred_ai_provider": "openrouter"},
        headers=professor_headers,
    )
    assert online.json()["internet_required"] is True

    local = client.put(
        f"{API}/settings",
        json={"ai_enabled": True, "preferred_ai_provider": "ollama"},
        headers=professor_headers,
    )
    assert local.json()["internet_required"] is False

    disabled = client.put(
        f"{API}/settings",
        json={"ai_enabled": False, "preferred_ai_provider": "openrouter"},
        headers=professor_headers,
    )
    assert disabled.json()["internet_required"] is False


def test_settings_are_isolated_between_teachers(client: TestClient) -> None:
    first = _register(client, name="First")
    second = _register(client, name="Second")

    updated = client.put(
        f"{API}/settings",
        json={"default_lecture_minutes": 120, "email_weekday": 3},
        headers=first,
    )
    assert updated.status_code == 200

    first_settings = _get(client, first)
    second_settings = _get(client, second)
    assert first_settings["default_lecture_minutes"] == 120
    assert second_settings["default_lecture_minutes"] == settings.default_topic_minutes
    assert second_settings["email_weekday"] == 0
    assert first_settings["id"] != second_settings["id"]
    assert first_settings["user_id"] != second_settings["user_id"]


def test_settings_require_authentication(client: TestClient) -> None:
    assert client.get(f"{API}/settings").status_code == 401
    assert client.put(f"{API}/settings", json={"ai_enabled": True}).status_code == 401
    assert (
        client.post(f"{API}/settings/ai-config", json={"provider": "openrouter"}).status_code
        == 401
    )
    assert (
        client.post(f"{API}/settings/ai-test", json={"provider": "openrouter"}).status_code
        == 401
    )


def test_ai_config_endpoint_persists_settings(
    client: TestClient,
    professor_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The endpoint mutates process-wide Settings; restore them afterwards so an
    # enabled provider cannot leak into later tests.
    for attribute in ("llm_provider", "openrouter_api_key", "openrouter_model"):
        monkeypatch.setattr(settings, attribute, getattr(settings, attribute))
    response = client.post(
        f"{API}/settings/ai-config",
        json={
            "provider": "openrouter",
            "api_key": "sk-or-v1-testkey12345",
            "model": "google/gemini-2.0-flash-001",
        },
        headers=professor_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "openrouter"
    assert body["model"] == "google/gemini-2.0-flash-001"
    assert body["openrouter_configured"] is True
    assert body["llm_enabled"] is True
    assert body["ai_config_writable"] is True

    # Check that settings GET reflects updated status
    settings_body = _get(client, professor_headers)
    assert settings_body["ai_enabled"] is True
    assert settings_body["preferred_ai_provider"] == "openrouter"
    assert settings_body["openrouter_configured"] is True


def test_ai_test_endpoint_without_key_reports_clean_error(
    client: TestClient, professor_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    response = client.post(
        f"{API}/settings/ai-test",
        json={"provider": "openrouter", "api_key": ""},
        headers=professor_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "No OpenRouter API key" in body["message"]
