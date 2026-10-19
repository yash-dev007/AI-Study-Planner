"""Settings persistence and safe configuration reporting."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import User, UserSettings
from app.schemas.settings import UserSettingsRead, UserSettingsUpdate


def get_or_create_user_settings(db: Session, user: User) -> UserSettings:
    row = db.execute(
        select(UserSettings).where(UserSettings.user_id == user.id)
    ).scalar_one_or_none()
    if row is None:
        row = UserSettings(
            user_id=user.id,
            default_lecture_minutes=settings.default_topic_minutes,
            default_teaching_days=[],
        )
        db.add(row)
        db.flush()
    return row


def update_user_settings(
    db: Session, *, user: User, payload: UserSettingsUpdate
) -> UserSettings:
    row = get_or_create_user_settings(db, user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    db.flush()
    return row


def settings_read(row: UserSettings) -> UserSettingsRead:
    provider = row.preferred_ai_provider
    return UserSettingsRead(
        id=row.id,
        user_id=row.user_id,
        default_lecture_minutes=row.default_lecture_minutes,
        default_teaching_days=list(row.default_teaching_days or []),
        weekly_email_enabled=row.weekly_email_enabled,
        email_weekday=row.email_weekday,
        email_hour_local=row.email_hour_local,
        ai_enabled=row.ai_enabled,
        preferred_ai_provider=provider,
        preferred_ai_model=row.preferred_ai_model,
        openrouter_configured=settings.openrouter_configured,
        # Ollama is a local endpoint; its presence is enough to offer the option.
        ollama_configured=bool(settings.ollama_base_url),
        smtp_configured=settings.smtp_configured,
        internet_required=bool(row.ai_enabled and provider == "openrouter"),
    )
