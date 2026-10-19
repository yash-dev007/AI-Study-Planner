"""Per-teacher settings. Secrets are never part of these payloads."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

AiProvider = Literal["none", "openrouter", "ollama"]


class UserSettingsUpdate(BaseModel):
    default_lecture_minutes: int | None = Field(default=None, ge=15, le=300)
    default_teaching_days: list[int] | None = Field(default=None, max_length=7)
    weekly_email_enabled: bool | None = None
    email_weekday: int | None = Field(default=None, ge=0, le=6)
    email_hour_local: int | None = Field(default=None, ge=0, le=23)
    ai_enabled: bool | None = None
    preferred_ai_provider: AiProvider | None = None
    preferred_ai_model: str | None = Field(default=None, max_length=120)

    @field_validator("default_teaching_days")
    @classmethod
    def valid_weekdays(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        days = sorted(set(value))
        if any(day < 0 or day > 6 for day in days):
            raise ValueError("Teaching days must be between 0 (Monday) and 6 (Sunday).")
        return days


class UserSettingsRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    default_lecture_minutes: int
    default_teaching_days: list[int]
    weekly_email_enabled: bool
    email_weekday: int
    email_hour_local: int
    ai_enabled: bool
    preferred_ai_provider: AiProvider
    preferred_ai_model: str
    # Configuration status only; never the secret itself.
    openrouter_configured: bool
    ollama_configured: bool
    smtp_configured: bool
    internet_required: bool
