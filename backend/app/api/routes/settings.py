"""Teacher settings routes."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.settings import UserSettingsRead, UserSettingsUpdate
from app.services.settings import (
    get_or_create_user_settings,
    settings_read,
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
