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
# Migration registry — add new tuples at the end; never reorder existing ones.
# ---------------------------------------------------------------------------

ALL_MIGRATIONS: list[tuple[str, str]] = [
    ("001_initial", MIGRATION_001_INITIAL),
]
