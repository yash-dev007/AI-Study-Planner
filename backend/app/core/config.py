"""Application settings loaded from environment variables."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# The development placeholder. Production must override it; the validator below
# refuses to start with a published secret.
DEFAULT_JWT_SECRET = "change-me-in-production-please-use-a-long-random-value"
SUPPORTED_JWT_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Only ``backend/.env`` is authoritative. Loading the repository root
        # ``.env`` as a second file would silently override every local value
        # (pydantic-settings gives later files priority), including keys saved
        # from the Settings UI.
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Adaptive Syllabus API"
    app_env: str = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"
    db_echo: bool = False

    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    access_token_cookie: str = "access_token"

    # SMTP is optional: with no host the weekly email feature reports as
    # "not configured" and every other feature keeps working.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_use_tls: bool = True

    # NoDecode: pydantic-settings would otherwise JSON-decode this field before
    # validation, which breaks the documented comma-separated form and rejects
    # an empty value (e.g. `CORS_ORIGINS=` in compose). The validator below
    # handles the raw string instead.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    storage_dir: Path = BASE_DIR / "storage"
    # The professor-facing UI (plain HTML/CSS/JS) served by this app at /app.
    frontend_dir: Path | None = None
    max_upload_mb: int = 10
    max_extracted_chars: int = 120_000
    # LLM configuration. OpenRouter is preferred.
    llm_provider: str = "openrouter"
    llm_timeout_seconds: float = 60.0
    openrouter_api_key: str | None = None
    openrouter_model: str = "google/gemini-2.0-flash-001"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    default_topic_minutes: int = 60
    min_topic_minutes: int = 15
    max_topic_minutes: int = 480

    rate_limit_auth_per_minute: int = 20
    rate_limit_upload_per_minute: int = 10
    rate_limit_default_per_minute: int = 240
    # Only enable behind a reverse proxy that sets X-Forwarded-For itself.
    # Otherwise clients can spoof the header and bypass per-IP throttling.
    trust_forwarded_for: bool = False

    # Local development seed data. Disabled by default to keep database clean.
    seed_demo_data: bool = False
    demo_professor_name: str = "Dr. Demo Professor"
    demo_professor_email: str = "professor@example.edu"
    demo_professor_password: str = "professor123"
    demo_course_name: str = "Data Structures and Algorithms"
    demo_course_code: str = "CS201"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        """Accept `a,b` or JSON-list env values; empty means "no CORS origins"."""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError:
                    return [text]
                return decoded if isinstance(decoded, list) else [text]
            return [part.strip() for part in text.split(",") if part.strip()]
        return value

    @field_validator("storage_dir", mode="after")
    @classmethod
    def resolve_storage(cls, value: Path) -> Path:
        return value if value.is_absolute() else (BASE_DIR / value).resolve()

    @property
    def resolved_frontend_dir(self) -> Path:
        """Where the HTML/CSS/JS frontend lives (overridable for containers)."""

        if self.frontend_dir is not None:
            return (
                self.frontend_dir
                if self.frontend_dir.is_absolute()
                else (BASE_DIR.parent / self.frontend_dir).resolve()
            )
        return BASE_DIR.parent / "frontend"

    @model_validator(mode="after")
    def validate_security_settings(self) -> Settings:
        """Fail closed on insecure production configuration."""

        if self.jwt_algorithm not in SUPPORTED_JWT_ALGORITHMS:
            raise ValueError(
                "JWT_ALGORITHM must be one of " + ", ".join(sorted(SUPPORTED_JWT_ALGORITHMS))
            )
        if self.is_production:
            if self.jwt_secret == DEFAULT_JWT_SECRET or len(self.jwt_secret) < 32:
                raise ValueError(
                    "JWT_SECRET must be a long random value in production; the development "
                    "default is public knowledge and would let anyone forge sessions."
                )
            if "*" in self.cors_origins:
                raise ValueError("CORS_ORIGINS must list explicit origins in production.")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def llm_enabled(self) -> bool:
        """Whether AI calls are enabled by the explicit provider choice.

        ``LLM_PROVIDER=none`` (and any unknown value) means "disabled" even
        when a key happens to be present in the environment: the provider
        choice is the contract, never an implicit key sniff.
        """

        provider = (self.llm_provider or "").strip().lower()
        if provider == "openrouter":
            return bool(self.openrouter_api_key)
        if provider == "gemini":
            return bool(self.gemini_api_key)
        return provider == "ollama"

    @property
    def openrouter_configured(self) -> bool:
        return bool(self.openrouter_api_key)

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
