"""Application configuration loaded from environment variables / .env file.

INFRA-01: All credentials stored via env-based secret management; never in source.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Quiz-funnel landing pages (funnel v3): page_view_lp -> quiz_complete ->
# lead_submit is tracked only for these lp_slug values. Kept as a plain module
# constant (not a Settings field) since it's a fixed product taxonomy, not
# environment-configurable -- mirrors the ALL_MIGRATIONS registry pattern in
# src/db/schema.py. Consumers (dashboard queries, ingestion) take this as an
# explicit parameter rather than importing Settings, so they stay decoupled
# from the required-env-var Settings class (see src/dashboard/settings.py
# "standalone" rationale).
QUIZ_LP_SLUGS: list[str] = ["routine-break", "big-feelings-type", "screen-kid"]


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

    # ---- Report scheduling (Phase 2) ----
    meta_ingest_hour: int = 2
    daily_report_hour: int = 9
    heartbeat_url: str | None = None

    # ---- Alert thresholds (Phase 2, D-16) ----
    alert_spend_spike_pct: float = 50.0
    alert_roas_floor: float = 1.0
    alert_zero_conv_spend_threshold: float = 50.0
    alert_budget_pacing_pct: float = 20.0
    alert_cpc_spike_multiplier: float = 2.0

    # ---- GA4 (Phase 3) ----
    ga4_property_id: str | None = None
    ga4_service_account_json: Path | None = None
    ga4_conversion_event: str = "purchase"

    # ---- Shopify (funnel-v3: preorder purchases) ----
    # Both unset (default) => src/shopify/ingest.py is a clean no-op — same graceful
    # degradation pattern as the other optional sources (Sheets/Stripe, Sentry).
    shopify_store_domain: str | None = None       # e.g. "shop.nowaplanet.com"
    shopify_admin_token: SecretStr | None = None  # custom-app Admin API access token
    shopify_api_version: str = "2025-01"

    # ---- Funnel v3: config-driven event/conversion definitions ----
    # New preorder funnel (ads -> LP -> GA4 session -> reserve click -> add_to_cart ->
    # begin_checkout -> purchase) + quiz funnel (page_view_lp -> quiz_complete ->
    # lead_submit). Consumed by the new GA4 events ingestion (src/ga4/ingest.py) so it
    # stops hardcoding event names. Existing FSD/Stripe dashboard pages are NOT
    # refactored to use these yet (out of scope for this data-layer change).
    primary_conversion_event: str = "begin_checkout"
    purchase_event: str = "purchase"
    lead_event: str = "lead_submit"
    ga4_event_list: str | list[str] = Field(
        default_factory=lambda: [
            "page_view_lp",
            "cta_click",
            "add_to_cart",
            "begin_checkout",
            "purchase",
            "lead_submit",
            "quiz_complete",
        ]
    )

    # ---- Anthropic (Phase 4) ----
    anthropic_api_key: SecretStr | None = None
    anthropic_monthly_budget_usd: float = 20.0

    # MMM: monetary value of one deposit in USD; 0.0 = report in deposits-per-$1000 units (D-09)
    deposit_value_usd: float = 0.0

    # ---- Google Sheets (optional — for stripe_payments pull) ----
    google_sheets_spreadsheet_id: str | None = None
    # Simplest setup: reuse the GA4 service account file (share the sheet with its email)
    google_service_account_json_path: str | None = None  # file path to service account JSON
    google_service_account_json: str | None = None       # full JSON string (alternative)
    google_oauth_token_path: str | None = None           # OAuth2 token file path

    # ---- Sentry (Phase 5) ----
    sentry_dsn: SecretStr | None = None
    sentry_environment: str = "production"

    # ---- App config ----
    db_path: Path = Path("/data/metrics.db")
    log_level: str = "INFO"
    report_timezone: str = "UTC"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,
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

    @field_validator("ga4_event_list", mode="before")
    @classmethod
    def _split_event_list_csv(cls, v: object) -> object:
        """Accept comma-separated event names from .env (e.g. "page_view_lp,cta_click")
        in addition to a JSON array, mirroring _split_csv above for chat/user ids."""
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return v  # let pydantic parse JSON
            return [x.strip() for x in stripped.split(",") if x.strip()]
        return v


def load_settings() -> Settings:
    """Load settings once at boot. Raises ValidationError immediately on missing required config."""
    return Settings()  # type: ignore[call-arg]
