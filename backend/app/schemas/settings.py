"""Per-teacher settings. Secrets are never part of these payloads."""

from __future__ import annotations

import re
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AiProvider = Literal["none", "openrouter", "gemini", "ollama"]

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
# Provider model identifiers (e.g. "google/gemini-2.0-flash-001", "llama3.1:8b",
# "@preset/name"). Restricting the character set also keeps the identifier safe to
# place in a request path.
_MODEL_NAME = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9._:/@+-]*$")


class UserSettingsUpdate(BaseModel):
    # Unknown fields are rejected instead of silently ignored: a typo in a client
    # payload must be visible, not a no-op.
    model_config = ConfigDict(extra="forbid")

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


class AiConfigUpdate(BaseModel):
    """Payload to configure AI provider, API key, and model directly from UI."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["openrouter", "gemini", "ollama"] = "openrouter"
    api_key: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=120)

    @field_validator("api_key", "model", mode="before")
    @classmethod
    def no_control_characters(cls, value: object) -> object:
        """Reject newlines and control characters.

        The value is written into ``backend/.env``; an embedded newline would
        become a second environment variable.
        """

        if isinstance(value, str):
            if _CONTROL_CHARS.search(value):
                raise ValueError("Value must not contain control characters or line breaks.")
            return value.strip() or None
        return value

    @field_validator("model")
    @classmethod
    def valid_model_name(cls, value: str | None) -> str | None:
        if value is not None and not _MODEL_NAME.match(value):
            raise ValueError(
                "Model names may only contain letters, digits, and . _ : / @ + -"
            )
        return value


class AiConfigRead(BaseModel):
    provider: str
    model: str
    llm_enabled: bool
    openrouter_configured: bool
    openrouter_model: str
    gemini_configured: bool
    gemini_model: str
    ollama_configured: bool
    # True only when this deployment accepts provider keys from the UI. In
    # production the operator provisions them through the environment.
    ai_config_writable: bool = True
    message: str = "AI configuration updated successfully."


class AiTestResponse(BaseModel):
    ok: bool
    message: str
    provider: str


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
    llm_enabled: bool
    # False when keys are environment-only (production) and the UI must offer
    # per-teacher enablement instead of a key form.
    ai_config_writable: bool
    # Configuration status only; never the secret itself.
    openrouter_configured: bool
    openrouter_model: str
    gemini_configured: bool
    gemini_model: str
    ollama_configured: bool
    smtp_configured: bool
    internet_required: bool
