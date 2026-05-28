"""Dashboard-specific settings.

Standalone — does not import src.config (which requires TELEGRAM_BOT_TOKEN).
Reads from the same .env file as the bot.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class DashboardSettings(BaseSettings):
    db_path: Path = Path("./data/metrics.db")
    anthropic_api_key: str = ""
    dashboard_password: str = ""
    report_timezone: str = "UTC"
    anthropic_monthly_budget_usd: float = 20.0
    cpd_target: float = 0.0
    # MMM: monetary value of one deposit in USD; 0.0 = report in deposits-per-$1000 units (D-09)
    deposit_value_usd: float = 0.0
    # Meta account ID — used to generate Ads Manager deep-links in the UI
    meta_ad_account_id: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,
    )
