"""Settings persistence, AI configuration, and safe configuration reporting."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR, settings
from app.core.errors import ForbiddenError
from app.models.entities import User, UserSettings
from app.schemas.settings import (
    AiConfigRead,
    AiConfigUpdate,
    AiTestResponse,
    UserSettingsRead,
    UserSettingsUpdate,
)

logger = logging.getLogger(__name__)

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
MAX_PROVIDER_MESSAGE_CHARS = 300


def _env_value(value: str) -> str:
    """Make a value safe to write as a single quoted ``.env`` entry.

    A newline inside the value would end the entry and turn the remainder into a
    new environment variable, so control characters are removed.
    """

    return _CONTROL_CHARS.sub("", value).replace('"', '\\"')


def _redact(text: str, *secrets: str | None) -> str:
    """Remove provider credentials from text that is returned to the client."""

    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def _provider_error_message(body: object, fallback: str) -> str:
    """Extract ``error.message`` from a provider error body without assuming a shape."""

    message: object = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
        elif isinstance(error, str):
            message = error
        if message is None:
            message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        message = fallback
    return message.strip()[:MAX_PROVIDER_MESSAGE_CHARS]


def _update_env_file(key_values: dict[str, str]) -> None:
    """Safely update environment variables in backend/.env without exposing secrets in logs."""
    env_path = BASE_DIR / ".env"
    lines: list[str] = []
    if env_path.exists():
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            logger.warning("Could not read existing .env file: %s", exc)
            lines = []

    updated_keys: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        k, _ = line.split("=", 1)
        k = k.strip()
        if k in key_values:
            new_lines.append(f'{k}="{_env_value(key_values[k])}"')
            updated_keys.add(k)
        else:
            new_lines.append(line)

    for k, v in key_values.items():
        if k not in updated_keys:
            new_lines.append(f'{k}="{_env_value(v)}"')

    # Write-then-rename so a crash never leaves a truncated secrets file behind.
    env_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=str(env_path.parent), prefix=".env.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("\n".join(new_lines) + "\n")
        with contextlib.suppress(Exception):
            Path(temporary).chmod(0o600)
        os.replace(temporary, env_path)
    except BaseException:
        with contextlib.suppress(Exception):
            Path(temporary).unlink()
        raise


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


def save_ai_config(
    db: Session, *, user: User, payload: AiConfigUpdate
) -> AiConfigRead:
    """Save AI key and model from UI into backend/.env and active settings."""
    if settings.is_production:
        # A self-registered professor must not be able to replace the server-wide
        # provider key. Production keys are provisioned through the environment.
        raise ForbiddenError(
            "AI credentials are managed by the server environment in production.",
            code="AI_CONFIG_UI_DISABLED",
        )
    row = get_or_create_user_settings(db, user)
    row.ai_enabled = True
    row.preferred_ai_provider = payload.provider

    env_updates: dict[str, str] = {"LLM_PROVIDER": payload.provider}
    cleaned_key = (payload.api_key or "").strip()

    if payload.provider == "openrouter":
        if cleaned_key:
            env_updates["OPENROUTER_API_KEY"] = cleaned_key
            settings.openrouter_api_key = cleaned_key
        if payload.model:
            model = payload.model.strip()
            env_updates["OPENROUTER_MODEL"] = model
            settings.openrouter_model = model
            row.preferred_ai_model = model
        else:
            row.preferred_ai_model = settings.openrouter_model
        settings.llm_provider = "openrouter"

    elif payload.provider == "gemini":
        if cleaned_key:
            env_updates["GEMINI_API_KEY"] = cleaned_key
            settings.gemini_api_key = cleaned_key
        if payload.model:
            model = payload.model.strip()
            env_updates["GEMINI_MODEL"] = model
            settings.gemini_model = model
            row.preferred_ai_model = model
        else:
            row.preferred_ai_model = settings.gemini_model
        settings.llm_provider = "gemini"

    elif payload.provider == "ollama":
        if payload.model:
            model = payload.model.strip()
            env_updates["OLLAMA_MODEL"] = model
            settings.ollama_model = model
            row.preferred_ai_model = model
        else:
            row.preferred_ai_model = settings.ollama_model
        settings.llm_provider = "ollama"

    # Never rewrite the repository's secrets file from a test run: the suite would
    # overwrite the developer's real provider key with a fixture value.
    if env_updates and settings.app_env != "test":
        _update_env_file(env_updates)

    db.flush()

    active_model = (
        settings.openrouter_model
        if payload.provider == "openrouter"
        else settings.gemini_model
        if payload.provider == "gemini"
        else settings.ollama_model
    )

    return AiConfigRead(
        provider=payload.provider,
        model=active_model,
        llm_enabled=settings.llm_enabled,
        openrouter_configured=settings.openrouter_configured,
        openrouter_model=settings.openrouter_model,
        gemini_configured=settings.gemini_configured,
        gemini_model=settings.gemini_model,
        ollama_configured=bool(settings.ollama_base_url),
        ai_config_writable=True,
        message=(
            f"Successfully configured {payload.provider.title()} AI provider with model "
            f"{active_model}."
        ),
    )


def test_ai_connection(payload: AiConfigUpdate) -> AiTestResponse:
    """Test AI provider connectivity with provided or stored credentials."""
    provider = payload.provider.lower()

    if provider == "openrouter":
        key = (payload.api_key or "").strip() or settings.openrouter_api_key
        if not key:
            return AiTestResponse(
                ok=False,
                message="No OpenRouter API key provided or configured.",
                provider="openrouter",
            )
        model = (payload.model or "").strip() or settings.openrouter_model
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(
                    f"{settings.openrouter_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "Respond with ok"}],
                        "max_tokens": 10,
                    },
                )
                if resp.status_code == 200:
                    return AiTestResponse(
                        ok=True,
                        message=f"Successfully connected to OpenRouter! (Model: {model})",
                        provider="openrouter",
                    )
                error_body: object = None
                with contextlib.suppress(Exception):
                    error_body = resp.json()
                msg = _provider_error_message(error_body, resp.text[:200])
                return AiTestResponse(
                    ok=False,
                    message=_redact(
                        f"OpenRouter authentication or model error "
                        f"({resp.status_code}): {msg}",
                        key,
                    ),
                    provider="openrouter",
                )
        except Exception as exc:
            return AiTestResponse(
                ok=False,
                message=_redact(f"Failed to connect to OpenRouter: {exc}", key),
                provider="openrouter",
            )

    elif provider == "gemini":
        key = (payload.api_key or "").strip() or settings.gemini_api_key
        if not key:
            return AiTestResponse(
                ok=False,
                message="No Gemini API key provided or configured.",
                provider="gemini",
            )
        model = (payload.model or "").strip() or settings.gemini_model
        # The key travels in a header: a query-string key leaks into exception text,
        # which this endpoint returns to the caller.
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{quote(model, safe='/._:-@+')}:generateContent"
        )
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(
                    url,
                    headers={"x-goog-api-key": key},
                    json={"contents": [{"parts": [{"text": "Respond with ok"}]}]},
                )
                if resp.status_code == 200:
                    return AiTestResponse(
                        ok=True,
                        message=f"Successfully connected to Google Gemini! (Model: {model})",
                        provider="gemini",
                    )
                gemini_error: object = None
                with contextlib.suppress(Exception):
                    gemini_error = resp.json()
                msg = _provider_error_message(gemini_error, resp.text[:200])
                return AiTestResponse(
                    ok=False,
                    message=_redact(f"Gemini API error ({resp.status_code}): {msg}", key),
                    provider="gemini",
                )
        except Exception as exc:
            return AiTestResponse(
                ok=False,
                message=_redact(f"Failed to connect to Gemini: {exc}", key),
                provider="gemini",
            )

    elif provider == "ollama":
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{settings.ollama_base_url}/api/tags")
                if resp.status_code == 200:
                    return AiTestResponse(
                        ok=True,
                        message=(
                            f"Successfully reached local Ollama server at "
                            f"{settings.ollama_base_url}!"
                        ),
                        provider="ollama",
                    )
                return AiTestResponse(
                    ok=False,
                    message=f"Ollama server returned status {resp.status_code}",
                    provider="ollama",
                )
        except Exception as exc:
            return AiTestResponse(
                ok=False,
                message=f"Failed to reach Ollama at {settings.ollama_base_url}: {exc}",
                provider="ollama",
            )

    return AiTestResponse(
        ok=False,
        message=f"Unsupported provider: {provider}",
        provider=provider,
    )


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
        llm_enabled=settings.llm_enabled,
        ai_config_writable=not settings.is_production,
        openrouter_configured=settings.openrouter_configured,
        openrouter_model=settings.openrouter_model,
        gemini_configured=settings.gemini_configured,
        gemini_model=settings.gemini_model,
        ollama_configured=bool(settings.ollama_base_url),
        smtp_configured=settings.smtp_configured,
        internet_required=bool(row.ai_enabled and provider in {"openrouter", "gemini"}),
    )
