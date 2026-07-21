"""SQLite schema DDL constants for the Ads Reporting Agent.

All tables are defined as CREATE TABLE IF NOT EXISTS so the DDL is safe to
re-run.  Migration versioning is handled by src.db.migrations via ALL_MIGRATIONS.

Data model rules (CLAUDE.md):
  - Meta conversion columns use the ``meta_`` prefix.
  - GA4 conversion columns use the ``ga4_`` prefix.
  - Never blend / average Meta and GA4 conversion numbers.
  - Meta <-> GA4 join key: exact UTM campaign name match only.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Migration 001 — Phase 1 canonical schema
# ---------------------------------------------------------------------------

MIGRATION_001_INITIAL: str = """
-- Tracks which migrations have been applied to this database.
CREATE TABLE IF NOT EXISTS schema_version (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Dimension: advertising campaigns (currently Meta Ads; extensible to Google Ads).
CREATE TABLE IF NOT EXISTS campaigns (
    id          TEXT PRIMARY KEY,
    source      TEXT NOT NULL,                         -- 'meta_ads' (room for 'google_ads' later)
    name        TEXT NOT NULL,
    status      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Fact: daily Meta Ads performance.
--
-- PK widened to (campaign_id, date, ad_set_id, ad_id) per RESEARCH Pattern 4 so
-- Phase 2 META-03 (per-adset / per-ad granularity) does NOT require a costly SQLite
-- table-rebuild migration.  Phase 1 ingestion writes campaign-level rows with sentinel
-- defaults ad_set_id='' and ad_id=''.  SQLite NULL != NULL in composite PKs, so
-- NOT NULL DEFAULT '' makes UPSERT deterministic (INFRA-03).
CREATE TABLE IF NOT EXISTS ad_metrics (
    campaign_id              TEXT NOT NULL,
    date                     TEXT NOT NULL,           -- ISO YYYY-MM-DD in ad-account timezone
    ad_set_id                TEXT NOT NULL DEFAULT '', -- '' = campaign-level row (Phase 1); real ad-set id in Phase 2 META-03
    ad_id                    TEXT NOT NULL DEFAULT '', -- '' = campaign-level row (Phase 1); real ad id in Phase 2 META-03
    spend                    REAL,
    impressions              INTEGER,
    clicks                   INTEGER,
    ctr                      REAL,
    cpc                      REAL,
    cpm                      REAL,
    roas                     REAL,
    meta_purchases_7dclick   INTEGER,                 -- CLAUDE.md: meta_ prefix for Meta conversion fields
    meta_cost_per_purchase   REAL,                    -- CLAUDE.md: meta_ prefix for Meta conversion fields
    reach                    INTEGER,
    frequency                REAL,
    fetched_at               TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (campaign_id, date, ad_set_id, ad_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ad_metrics_date ON ad_metrics(date);

-- Fact: daily GA4 performance.
--
-- Join key to campaigns: campaign_utm must be an exact match of utm_campaign
-- (no fuzzy matching — CLAUDE.md data model rule).
CREATE TABLE IF NOT EXISTS ga4_metrics (
    campaign_utm              TEXT NOT NULL,           -- utm_campaign value; exact-match join to campaigns.name
    date                      TEXT NOT NULL,
    sessions                  INTEGER,
    users                     INTEGER,
    new_users                 INTEGER,
    bounce_rate               REAL,
    avg_engagement_time       REAL,
    ga4_purchases_lastclick   INTEGER,                -- CLAUDE.md: ga4_ prefix for GA4 conversion fields
    fetched_at                TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (campaign_utm, date)
);
CREATE INDEX IF NOT EXISTS idx_ga4_metrics_date ON ga4_metrics(date);
CREATE INDEX IF NOT EXISTS idx_ga4_metrics_campaign ON ga4_metrics(campaign_utm);

-- Multi-turn conversation persistence for Phase 4 (Claude tool use).
CREATE TABLE IF NOT EXISTS bot_conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    message     TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('user','assistant','system','tool')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_bot_conv_chat ON bot_conversations(chat_id, created_at DESC);

-- Operational log: one row per ingestion run (Phase 2 writes here).
CREATE TABLE IF NOT EXISTS ingestion_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL CHECK (status IN ('success','partial','failed','running')),
    rows_upserted INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingestion_log_source ON ingestion_log(source, started_at DESC);
"""

# ---------------------------------------------------------------------------
# Migration 002 — Phase 2: alert deduplication log
# ---------------------------------------------------------------------------

MIGRATION_002_PHASE2: str = """
-- Alert deduplication: one alert per campaign per alert-type per calendar day.
CREATE TABLE IF NOT EXISTS alert_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type   TEXT NOT NULL,
    campaign_id  TEXT NOT NULL,
    date         TEXT NOT NULL,
    fired_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(alert_type, campaign_id, date)
);
CREATE INDEX IF NOT EXISTS idx_alert_log_date ON alert_log(date DESC);
"""

# ---------------------------------------------------------------------------
# Migration 003 — Phase 3: GA4 landing pages table
# ---------------------------------------------------------------------------

MIGRATION_003_PHASE3: str = """
CREATE TABLE IF NOT EXISTS ga4_landing_pages (
    landing_page              TEXT NOT NULL,
    date                      TEXT NOT NULL,
    sessions                  INTEGER,
    total_users               INTEGER,
    ga4_purchases_lastclick   INTEGER,
    screen_page_views         INTEGER,
    avg_engagement_time       REAL,
    fetched_at                TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (landing_page, date)
);
CREATE INDEX IF NOT EXISTS idx_ga4_lp_date ON ga4_landing_pages(date);
CREATE INDEX IF NOT EXISTS idx_ga4_lp_page ON ga4_landing_pages(landing_page);
"""

# ---------------------------------------------------------------------------
# Migration 004 — Phase 4: Anthropic usage tracking
# ---------------------------------------------------------------------------

MIGRATION_004_PHASE4: str = """
CREATE TABLE IF NOT EXISTS anthropic_usage_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    request_at    TEXT NOT NULL DEFAULT (datetime('now')),
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0.0,
    chat_id       INTEGER,
    user_id       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_usage_log_month ON anthropic_usage_log(request_at);
"""

# ---------------------------------------------------------------------------
# Migration 005 — Phase 3: form_submit_deposit column
# ---------------------------------------------------------------------------

MIGRATION_005_FORM_SUBMIT: str = """
ALTER TABLE ad_metrics ADD COLUMN meta_form_submit_deposit INTEGER NOT NULL DEFAULT 0;
"""

# ---------------------------------------------------------------------------
# Migration 006 — Phase 8: MMM results (append-only)
# ---------------------------------------------------------------------------

MIGRATION_006_PHASE8: str = """
CREATE TABLE IF NOT EXISTS mmm_results (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date                 TEXT NOT NULL,
    weeks_of_data            INTEGER NOT NULL,
    media_pct                REAL NOT NULL,
    baseline_pct             REAL NOT NULL,
    incremental_roas_per_1k  REAL,
    optimal_daily_spend      REAL NOT NULL,
    theta                    REAL NOT NULL,
    km                       REAL NOT NULL,
    n                        REAL NOT NULL,
    maturity_label           TEXT NOT NULL,
    created_at               TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_mmm_results_run_date ON mmm_results(run_date DESC);
"""

# ---------------------------------------------------------------------------
# Migration 007 — Ad changelog (change_history API)
# ---------------------------------------------------------------------------

MIGRATION_007_CHANGELOGS: str = """
CREATE TABLE IF NOT EXISTS ad_changelogs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    change_time     TEXT NOT NULL,
    object_id       TEXT NOT NULL,
    object_name     TEXT NOT NULL,
    object_type     TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    changed_fields  TEXT,
    old_value       TEXT,
    new_value       TEXT,
    actor_name      TEXT,
    fetched_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(object_id, change_time, event_type)
);
CREATE INDEX IF NOT EXISTS idx_ad_changelogs_time ON ad_changelogs(change_time DESC);
CREATE INDEX IF NOT EXISTS idx_ad_changelogs_object ON ad_changelogs(object_type, object_id);
"""

# ---------------------------------------------------------------------------
# Migration 008 — Stripe payments from Google Sheets
# ---------------------------------------------------------------------------

MIGRATION_008_STRIPE_PAYMENTS: str = """
CREATE TABLE IF NOT EXISTS stripe_payments (
    uid             TEXT PRIMARY KEY,
    submitted_at    TEXT NOT NULL,
    email           TEXT,
    source          TEXT,
    status          TEXT NOT NULL CHECK(status IN ('pending', 'paid')),
    session_id      TEXT,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_stripe_submitted ON stripe_payments(submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_stripe_source ON stripe_payments(source, status);
"""

# ---------------------------------------------------------------------------
# Migration 009 — Ad creatives metadata (style, format, thumbnail, URLs)
# ---------------------------------------------------------------------------

MIGRATION_009_AD_CREATIVES: str = """
CREATE TABLE IF NOT EXISTS ad_creatives (
    ad_id           TEXT PRIMARY KEY,
    ad_name         TEXT NOT NULL,
    adset_id        TEXT,
    campaign_id     TEXT,
    effective_status TEXT,
    ad_format       TEXT,
    ad_style        TEXT,
    thumbnail_url   TEXT,
    destination_url TEXT,
    preview_url     TEXT,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ad_creatives_campaign ON ad_creatives(campaign_id);
CREATE INDEX IF NOT EXISTS idx_ad_creatives_style ON ad_creatives(ad_style);
CREATE INDEX IF NOT EXISTS idx_ad_creatives_format ON ad_creatives(ad_format);
"""

# ---------------------------------------------------------------------------
# Migration 010 — Funnel v3: GA4 event-level ingestion
#
# Tracks raw GA4 event counts (page_view_lp, cta_click, add_to_cart,
# begin_checkout, purchase, lead_submit, quiz_complete, ...) segmented by the
# lp_slug custom dimension (routine, big-feelings, screen-anxious, ...).
# campaign_utm and lp_slug default to '' (not NULL) so the composite PK
# de-duplicates correctly — same rationale as ad_metrics ad_set_id/ad_id
# (SQLite NULL != NULL in a PRIMARY KEY).
# ---------------------------------------------------------------------------

MIGRATION_010_GA4_EVENTS: str = """
CREATE TABLE IF NOT EXISTS ga4_events (
    event_name    TEXT NOT NULL,
    date          TEXT NOT NULL,
    campaign_utm  TEXT NOT NULL DEFAULT '',
    lp_slug       TEXT NOT NULL DEFAULT '',
    event_count   INTEGER,
    fetched_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (event_name, date, campaign_utm, lp_slug)
);
CREATE INDEX IF NOT EXISTS idx_ga4_events_date ON ga4_events(date);
CREATE INDEX IF NOT EXISTS idx_ga4_events_campaign ON ga4_events(campaign_utm);
CREATE INDEX IF NOT EXISTS idx_ga4_events_lp_slug ON ga4_events(lp_slug);
"""

# ---------------------------------------------------------------------------
# Migration 011 — Funnel v3: Meta landing_page_views, video hook/hold metrics,
# and Shopify-funnel actions (InitiateCheckout / AddToCart / Lead) on ad_metrics.
#
# All columns nullable (no DEFAULT) — CLAUDE.md graceful-degradation rule:
# a field that errors or is unavailable for a given ad account degrades to NULL
# rather than failing the whole ingest.
# ---------------------------------------------------------------------------

MIGRATION_011_META_FUNNEL_V3: str = """
ALTER TABLE ad_metrics ADD COLUMN landing_page_views INTEGER;
ALTER TABLE ad_metrics ADD COLUMN video_3s_views INTEGER;
ALTER TABLE ad_metrics ADD COLUMN video_thruplay INTEGER;
ALTER TABLE ad_metrics ADD COLUMN meta_begin_checkout INTEGER;
ALTER TABLE ad_metrics ADD COLUMN meta_cost_per_begin_checkout REAL;
ALTER TABLE ad_metrics ADD COLUMN meta_add_to_cart INTEGER;
ALTER TABLE ad_metrics ADD COLUMN meta_leads INTEGER;
"""

# ---------------------------------------------------------------------------
# Migration 012 — Funnel v3: Shopify orders (preorder purchases)
#
# utm_* / lp_slug / landing_site / referring_site default to '' (not NULL) —
# parsed from the order's landing_site query string (src/shopify/client.py).
# ---------------------------------------------------------------------------

MIGRATION_012_SHOPIFY_ORDERS: str = """
CREATE TABLE IF NOT EXISTS shopify_orders (
    order_id          TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    order_date        TEXT NOT NULL,
    total_price       REAL,
    financial_status  TEXT,
    utm_source        TEXT NOT NULL DEFAULT '',
    utm_campaign      TEXT NOT NULL DEFAULT '',
    utm_content       TEXT NOT NULL DEFAULT '',
    lp_slug           TEXT NOT NULL DEFAULT '',
    landing_site      TEXT NOT NULL DEFAULT '',
    referring_site    TEXT NOT NULL DEFAULT '',
    fetched_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_shopify_orders_date ON shopify_orders(order_date);
CREATE INDEX IF NOT EXISTS idx_shopify_orders_utm_campaign ON shopify_orders(utm_campaign);
CREATE INDEX IF NOT EXISTS idx_shopify_orders_lp_slug ON shopify_orders(lp_slug);
"""

# ---------------------------------------------------------------------------
# Migration 013 — Phase C: Meta Pixel health (per-event browser/server counts,
# dedup rate, EMQ score).
#
# emq_score is nullable and populated on a best-effort basis: the standard
# /{pixel_id}/stats Graph API endpoint (used for browser_count/server_count)
# does NOT expose event_match_quality (confirmed against the facebook-business
# SDK's AdsPixel field list — see src/meta/client.py fetch_pixel_emq docstring
# for the full research finding). EMQ is only available via the separate
# Dataset Quality API node, which requires Advanced Access to the Marketing
# API (an app-review-gated tier) beyond the basic token this project already
# uses for Insights. The column exists regardless so a manual / Playwright-
# based filler can populate it later without another migration.
# dedup_rate is likewise nullable — populated only when the (also best-effort)
# Dataset Quality call succeeds and returns a deduplication metric; else NULL.
# ---------------------------------------------------------------------------

MIGRATION_013_PIXEL_HEALTH: str = """
CREATE TABLE IF NOT EXISTS pixel_health (
    date            TEXT NOT NULL,
    event_name      TEXT NOT NULL,
    browser_count   INTEGER,
    server_count    INTEGER,
    dedup_rate      REAL,
    emq_score       REAL,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (date, event_name)
);
CREATE INDEX IF NOT EXISTS idx_pixel_health_date ON pixel_health(date);
"""

# ---------------------------------------------------------------------------
# Migration 014 — Exact property-wide GA4 daily session totals
#
# Fixes the session multi-counting bug in the old two-pass
# _fetch_landing_page_metrics_sync (src/ga4/client.py): any fetch grouped by a
# landing-page/pagePath dimension is at risk of counting a session once per
# distinct dimension value it touches. This table is fed by a dimensions=[date]
# / metrics=[sessions] report with NO other grouping -- the literal GA4
# property-wide "Sessions" total per day -- so it cannot exhibit that failure
# mode. date is the sole PK (one row per day, no landing-page breakdown);
# src/dashboard/db.py's get_total_sessions_daily / get_total_sessions_summary
# prefer this table, falling back to summing ga4_landing_pages when this table
# is empty/missing (e.g. an older DB that hasn't been re-backfilled yet).
# ---------------------------------------------------------------------------

MIGRATION_014_GA4_DAILY_TOTALS: str = """
CREATE TABLE IF NOT EXISTS ga4_daily_totals (
    date        TEXT PRIMARY KEY,
    sessions    INTEGER,
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# ---------------------------------------------------------------------------
# Migration registry — add new tuples at the end; never reorder existing ones.
# ---------------------------------------------------------------------------

ALL_MIGRATIONS: list[tuple[str, str]] = [
    ("001_initial", MIGRATION_001_INITIAL),
    ("002_phase2", MIGRATION_002_PHASE2),
    ("003_phase3", MIGRATION_003_PHASE3),
    ("004_phase4", MIGRATION_004_PHASE4),
    ("005_form_submit", MIGRATION_005_FORM_SUBMIT),
    ("006_phase8", MIGRATION_006_PHASE8),
    ("007_changelogs", MIGRATION_007_CHANGELOGS),
    ("008_stripe_payments", MIGRATION_008_STRIPE_PAYMENTS),
    ("009_ad_creatives", MIGRATION_009_AD_CREATIVES),
    ("010_ga4_events", MIGRATION_010_GA4_EVENTS),
    ("011_meta_funnel_v3", MIGRATION_011_META_FUNNEL_V3),
    ("012_shopify_orders", MIGRATION_012_SHOPIFY_ORDERS),
    ("013_pixel_health", MIGRATION_013_PIXEL_HEALTH),
    ("014_ga4_daily_totals", MIGRATION_014_GA4_DAILY_TOTALS),
]
