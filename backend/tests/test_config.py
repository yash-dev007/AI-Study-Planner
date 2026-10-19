"""Settings parsing rules that deployment files depend on.

`CORS_ORIGINS` is passed through docker-compose as a plain (possibly empty)
string, and the documented form in `.env.example` is comma-separated. Both must
work: pydantic-settings would otherwise JSON-decode the field before validation
and reject the whole application at import time.
"""

from __future__ import annotations

import pytest
from app.core.config import Settings

DEFAULTS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def build(monkeypatch: pytest.MonkeyPatch, value: str | None) -> Settings:
    """Construct settings from the environment only (ignore any local .env)."""

    if value is None:
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("CORS_ORIGINS", value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_defaults_apply_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    assert build(monkeypatch, None).cors_origins == DEFAULTS


def test_empty_value_means_no_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    """docker-compose passes `CORS_ORIGINS=` when the variable is not set."""

    assert build(monkeypatch, "").cors_origins == []


def test_comma_separated_value(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = build(monkeypatch, "http://localhost:5173, http://127.0.0.1:5173")
    assert settings.cors_origins == DEFAULTS


def test_json_list_value(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = build(monkeypatch, '["http://a.test","http://b.test"]')
    assert settings.cors_origins == ["http://a.test", "http://b.test"]
