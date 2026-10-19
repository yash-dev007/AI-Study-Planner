"""Phase 2: cookie sessions, profile editing, and password rotation."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.core.config import settings
from fastapi.testclient import TestClient

API = "/api/v1"
ORIGIN = settings.cors_origins[0]
PASSWORD = "ProfPassw0rd!"


def _register(
    client: TestClient,
    *,
    email: str | None = None,
    password: str = PASSWORD,
    name: str = "Professor",
) -> dict[str, Any]:
    response = client.post(
        f"{API}/auth/register",
        json={
            "name": name,
            "email": email or f"prof-{uuid.uuid4().hex[:8]}@example.edu",
            "password": password,
            "role": "professor",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# Cookie sessions
# --------------------------------------------------------------------------- #


def test_login_sets_a_hardened_http_only_cookie(client: TestClient) -> None:
    registration = _register(client)
    login = client.post(
        f"{API}/auth/login",
        json={"email": registration["user"]["email"], "password": PASSWORD},
    )
    assert login.status_code == 200, login.text
    set_cookie = login.headers.get("set-cookie", "")
    assert settings.access_token_cookie in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
    assert "path=/" in set_cookie.lower()
    # Local tests run over plain HTTP, so the cookie must not be Secure there.
    assert "secure" not in set_cookie.lower()
    assert client.cookies.get(settings.access_token_cookie) is not None


def test_the_cookie_alone_authenticates_requests(client: TestClient) -> None:
    registration = _register(client)
    me = client.get(f"{API}/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["email"] == registration["user"]["email"]


def test_logout_clears_the_cookie(client: TestClient) -> None:
    _register(client)
    assert client.get(f"{API}/auth/me").status_code == 200

    response = client.post(f"{API}/auth/logout", headers={"Origin": ORIGIN})
    assert response.status_code == 204, response.text
    assert client.cookies.get(settings.access_token_cookie) is None
    assert client.get(f"{API}/auth/me").status_code == 401


def test_bearer_token_wins_over_a_stale_cookie(client: TestClient) -> None:
    first = _register(client, name="First Teacher")
    second = _register(client, name="Second Teacher")
    # The cookie in the client jar now belongs to the second teacher.
    me = client.get(f"{API}/auth/me", headers=_bearer(first["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == first["user"]["email"]
    assert me.json()["email"] != second["user"]["email"]


# --------------------------------------------------------------------------- #
# CSRF guard for cookie-authenticated writes
# --------------------------------------------------------------------------- #


def test_cookie_writes_from_a_foreign_origin_are_rejected(client: TestClient) -> None:
    _register(client)
    response = client.patch(
        f"{API}/auth/profile",
        json={"name": "Hijacked"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "CSRF_REJECTED"
    # The hostile request must not have changed anything.
    assert client.get(f"{API}/auth/me").json()["name"] == "Professor"


def test_cookie_writes_without_an_origin_are_rejected(client: TestClient) -> None:
    _register(client)
    response = client.patch(f"{API}/auth/profile", json={"name": "Hijacked"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_REJECTED"


@pytest.mark.parametrize(
    "extra_headers",
    [
        {"Origin": ORIGIN},
        {"Origin": "http://testserver"},
        {"Referer": f"{ORIGIN}/subjects"},
        {"Sec-Fetch-Site": "same-origin"},
    ],
    ids=["configured-origin", "request-host", "referer", "sec-fetch-site"],
)
def test_same_origin_cookie_writes_are_allowed(
    client: TestClient, extra_headers: dict[str, str]
) -> None:
    _register(client)
    response = client.patch(f"{API}/auth/profile", json={"name": "Renamed"}, headers=extra_headers)
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Renamed"


def test_a_null_origin_is_rejected(client: TestClient) -> None:
    _register(client)
    response = client.patch(
        f"{API}/auth/profile", json={"name": "Sandboxed"}, headers={"Origin": "null"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_REJECTED"


def test_bearer_writes_do_not_need_an_origin(client: TestClient) -> None:
    registration = _register(client)
    response = client.patch(
        f"{API}/auth/profile",
        json={"name": "API Client"},
        headers=_bearer(registration["access_token"]),
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "API Client"


def test_safe_methods_are_never_blocked(client: TestClient) -> None:
    _register(client)
    assert (
        client.get(f"{API}/auth/me", headers={"Origin": "https://evil.example"}).status_code == 200
    )


# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #


def test_profile_update_persists_every_field(client: TestClient) -> None:
    registration = _register(client)
    headers = _bearer(registration["access_token"])
    response = client.patch(
        f"{API}/auth/profile",
        json={
            "name": "  Dr. Ada Lovelace  ",
            "department": " Mathematics ",
            "college_name": "Analytical Engine College",
            "timezone": "Europe/London",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Dr. Ada Lovelace"
    assert body["department"] == "Mathematics"
    assert body["timezone"] == "Europe/London"

    me = client.get(f"{API}/auth/me", headers=headers).json()
    assert me["college_name"] == "Analytical Engine College"
    assert me["timezone"] == "Europe/London"


def test_profile_patch_only_touches_supplied_fields(client: TestClient) -> None:
    registration = _register(client)
    headers = _bearer(registration["access_token"])
    client.patch(
        f"{API}/auth/profile",
        json={"department": "Physics", "college_name": "Science Hall"},
        headers=headers,
    )
    response = client.patch(f"{API}/auth/profile", json={"name": "New Name"}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "New Name"
    assert body["department"] == "Physics"
    assert body["college_name"] == "Science Hall"


def test_profile_rejects_an_unknown_timezone(client: TestClient) -> None:
    registration = _register(client)
    response = client.patch(
        f"{API}/auth/profile",
        json={"timezone": "Mars/Olympus_Mons"},
        headers=_bearer(registration["access_token"]),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_profiles_are_isolated_between_teachers(client: TestClient) -> None:
    first = _register(client, name="First")
    second = _register(client, name="Second")
    client.patch(
        f"{API}/auth/profile",
        json={"name": "First Renamed", "department": "History"},
        headers=_bearer(first["access_token"]),
    )
    second_me = client.get(f"{API}/auth/me", headers=_bearer(second["access_token"])).json()
    assert second_me["name"] == "Second"
    assert second_me["department"] == ""
    assert second_me["email"] == second["user"]["email"]


def test_registration_rejects_a_duplicate_email(client: TestClient) -> None:
    registration = _register(client)
    duplicate = client.post(
        f"{API}/auth/register",
        json={
            "name": "Impostor",
            "email": registration["user"]["email"],
            "password": PASSWORD,
            "role": "professor",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"


# --------------------------------------------------------------------------- #
# Password rotation
# --------------------------------------------------------------------------- #


def test_changing_the_password_invalidates_old_tokens(client: TestClient) -> None:
    registration = _register(client)
    old_token = registration["access_token"]
    headers = _bearer(old_token)

    changed = client.post(
        f"{API}/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "BrandNewPass1!"},
        headers=headers,
    )
    assert changed.status_code == 200, changed.text
    new_token = changed.json()["access_token"]
    assert new_token != old_token

    stale = client.get(f"{API}/auth/me", headers=headers)
    assert stale.status_code == 401
    assert stale.json()["error"]["code"] == "TOKEN_REVOKED"

    fresh = client.get(f"{API}/auth/me", headers=_bearer(new_token))
    assert fresh.status_code == 200

    old_login = client.post(
        f"{API}/auth/login",
        json={"email": registration["user"]["email"], "password": PASSWORD},
    )
    assert old_login.status_code == 401
    new_login = client.post(
        f"{API}/auth/login",
        json={"email": registration["user"]["email"], "password": "BrandNewPass1!"},
    )
    assert new_login.status_code == 200


def test_a_wrong_current_password_changes_nothing(client: TestClient) -> None:
    registration = _register(client)
    headers = _bearer(registration["access_token"])
    response = client.post(
        f"{API}/auth/change-password",
        json={"current_password": "not-my-password", "new_password": "BrandNewPass1!"},
        headers=headers,
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "WRONG_PASSWORD"
    # The original session and password still work.
    assert client.get(f"{API}/auth/me", headers=headers).status_code == 200


def test_a_short_new_password_is_rejected(client: TestClient) -> None:
    registration = _register(client)
    response = client.post(
        f"{API}/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=_bearer(registration["access_token"]),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_cookie_sessions_need_a_same_origin_header_to_change_password(
    client: TestClient,
) -> None:
    _register(client)
    blocked = client.post(
        f"{API}/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "BrandNewPass1!"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "CSRF_REJECTED"

    allowed = client.post(
        f"{API}/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "BrandNewPass1!"},
        headers={"Origin": ORIGIN},
    )
    assert allowed.status_code == 200, allowed.text
    # The response rotates the cookie so the browser stays signed in.
    assert client.cookies.get(settings.access_token_cookie) is not None
    assert client.get(f"{API}/auth/me").status_code == 200
