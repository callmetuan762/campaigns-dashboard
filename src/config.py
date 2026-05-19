"""Application configuration loaded from environment variables / .env file.

INFRA-01: All credentials stored via env-based secret management; never in source.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ---- Telegram (required) ----
    telegram_bot_token: SecretStr
    # Union type (str | list[int]) prevents pydantic-settings from hard-failing on JSON decode
    # for CSV env values like "123,456". The field_validator below normalizes to list[int].
    telegram_allowed_chat_ids: str | list[int] = Field(default_factory=list)
    telegram_allowed_user_ids: str | list[int] = Field(default_factory=list)

    # ---- Meta Ads (Phase 2; declared now to fail fast on misspelled keys) ----
    meta_app_id: str | None = None
    meta_app_secret: SecretStr | None = None
    meta_access_token: SecretStr | None = None
    meta_ad_account_id: str | None = None

    # ---- GA4 (Phase 3) ----
    ga4_property_id: str | None = None
    ga4_service_account_json: Path | None = None

    # ---- Anthropic (Phase 4) ----
    anthropic_api_key: SecretStr | None = None

    # ---- App config ----
    db_path: Path = Path("/data/metrics.db")
    log_level: str = "INFO"
    report_timezone: str = "UTC"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("telegram_allowed_chat_ids", "telegram_allowed_user_ids", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Accept comma-separated values from .env (e.g. "123,456") in addition to JSON.

        Also handles pydantic-settings v2 behavior of pre-parsing bare integers via JSON
        (e.g. env value "789" becomes int 789 before reaching this validator).
        """
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return v  # let pydantic parse JSON
            return [int(x.strip()) for x in stripped.split(",") if x.strip()]
        return v


def load_settings() -> Settings:
    """Load settings once at boot. Raises ValidationError immediately on missing required config."""
    return Settings()  # type: ignore[call-arg]
