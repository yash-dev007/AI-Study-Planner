"""Teacher settings and AI configuration routes."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.settings import (
    AiConfigRead,
    AiConfigUpdate,
    AiTestResponse,
    UserSettingsRead,
    UserSettingsUpdate,
)
from app.services.settings import (
    get_or_create_user_settings,
    save_ai_config,
    settings_read,
    test_ai_connection,
    update_user_settings,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=UserSettingsRead, summary="My teaching and notification settings")
def read_settings(user: CurrentUser, db: DbSession) -> UserSettingsRead:
    row = get_or_create_user_settings(db, user)
    db.commit()
    return settings_read(row)


@router.put("", response_model=UserSettingsRead, summary="Update my settings")
def save_settings(
    payload: UserSettingsUpdate, user: CurrentUser, db: DbSession
) -> UserSettingsRead:
    row = update_user_settings(db, user=user, payload=payload)
    db.commit()
    return settings_read(row)


@router.post(
    "/ai-config",
    response_model=AiConfigRead,
    summary="Configure AI provider, model, and API key from UI",
)
def configure_ai(
    payload: AiConfigUpdate, user: CurrentUser, db: DbSession
) -> AiConfigRead:
    result = save_ai_config(db, user=user, payload=payload)
    db.commit()
    return result


@router.post(
    "/ai-test",
    response_model=AiTestResponse,
    summary="Test connection to the AI provider with current or provided credentials",
)
def test_ai(
    payload: AiConfigUpdate, _user: CurrentUser
) -> AiTestResponse:
    return test_ai_connection(payload)
