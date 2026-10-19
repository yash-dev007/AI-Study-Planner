"""Application settings loaded from environment variables."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BASE_DIR / ".env"),
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

    jwt_secret: str = "change-me-in-production-please-use-a-long-random-value"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480

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
    ocr_enabled: bool = True
    ocr_min_chars_per_page: int = 40
    tesseract_cmd: str | None = None

    # Provider-independent LLM configuration. "none" uses the deterministic
    # rule-based interpreter, which is also the automatic fallback.
    llm_provider: str = "none"
    llm_timeout_seconds: float = 60.0
    openrouter_api_key: str | None = None
    openrouter_model: str = "google/gemini-2.0-flash-001"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    default_topic_minutes: int = 60
    min_topic_minutes: int = 15
    max_topic_minutes: int = 480

    rate_limit_auth_per_minute: int = 20
    rate_limit_upload_per_minute: int = 10
    rate_limit_default_per_minute: int = 240

    # Local development seed data. Never enabled in production.
    seed_demo_data: bool = True
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

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def llm_enabled(self) -> bool:
        if self.llm_provider == "openrouter":
            return bool(self.openrouter_api_key)
        return self.llm_provider in {"ollama"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
