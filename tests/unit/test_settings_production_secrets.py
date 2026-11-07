"""Validation des secrets de production (Exigence 13.9)."""

from __future__ import annotations

import pytest
from app.core.config import Settings

_GOOD = {
    "APP_ENV": "production",
    "JWT_SECRET_KEY": "k9$Xv2-unique-jwt-value",
    "LLM_API_KEY": "sk-llm-unique-value",
    "EMBEDDING_API_KEY": "sk-emb-unique-value",
    "SMTP_PASSWORD": "smtp-unique-value",
    "DATABASE_URL": "postgresql+psycopg://app:Zq81!strong@db:5432/app",
}


def _build(**overrides: str) -> Settings:
    values = {**_GOOD, **overrides}
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_valid_production_secrets_accepted() -> None:
    assert _build().app_env == "production"


def test_all_offending_parameters_named_without_values() -> None:
    with pytest.raises(ValueError) as exc:
        _build(
            JWT_SECRET_KEY="change-me-in-production",
            LLM_API_KEY="",
            DATABASE_URL="postgresql+psycopg://partipolia:partipolia@db/app",
        )
    message = str(exc.value)
    for name in ("JWT_SECRET_KEY", "LLM_API_KEY", "DATABASE_URL"):
        assert name in message
    assert "EMBEDDING_API_KEY" not in message
    assert "change-me-in-production" not in message


def test_non_production_allows_empty_secrets() -> None:
    settings = Settings(_env_file=None, APP_ENV="development")  # type: ignore[call-arg]
    assert settings.app_env == "development"
