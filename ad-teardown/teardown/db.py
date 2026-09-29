"""SQLite storage. One file (data/teardown.db), no server.

Tables follow spec §5, trimmed for single-operator use. Two rules the schema enforces:
- Raw observations are immutable; derived rows (analyses) are versioned, never overwritten.
- Own-lane metrics live in `ad_metrics` only. Competitor ads never get a metrics row, so a
  chart can't accidentally blend real performance with creative-only data.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "teardown.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS brands (
  id text PRIMARY KEY,            -- slug from config/brands.yaml, e.g. 'nowa', 'yoto'
  name text NOT NULL,
  kind text NOT NULL,             -- own | competitor
  domain text,
  page_id text,
  parent_brand text               -- competitor -> the own brand it's benchmarked against
);

CREATE TABLE IF NOT EXISTS runs (
  id text PRIMARY KEY,
  status text NOT NULL,           -- queued|acquiring|processing|aggregating|completed|partial|failed|cancelled
  request text NOT NULL,          -- RunRequest JSON
  created_at text NOT NULL,
  finished_at text,
  budget_usd real,
  spent_usd real NOT NULL DEFAULT 0,
  counters text NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS ads (
  id text PRIMARY KEY,                    -- '<source>:<source_ad_id>'
  brand_id text NOT NULL REFERENCES brands(id),
  source text NOT NULL,                   -- meta_own | meta_adlib | csv
  source_ad_id text NOT NULL,
  source_url text,
  lane text NOT NULL,                     -- own | competitor
  campaign_id text, campaign_name text,
  adset_id text, adset_name text,
  ad_name text,
  active_status text NOT NULL DEFAULT 'unknown',  -- only what the source reports
  headline text, body text, description text, cta_text text,
  text_variants text NOT NULL DEFAULT '[]',       -- all bodies/titles for dynamic-creative ads
  destination_url_original text,
  destination_url_canonical text,
  format text NOT NULL DEFAULT 'unknown',         -- from media facts
  format_from_name text,                          -- from ad code (-CAR-/-VID-), cross-check only
  ad_code text,
  country text, language text,
  source_created_at text,
  first_seen_at text NOT NULL,
  last_seen_at text NOT NULL,
  raw_payload_key text NOT NULL,
  creative_group_id text,
  metadata text NOT NULL DEFAULT '{}',
  UNIQUE(source, source_ad_id)
);

CREATE TABLE IF NOT EXISTS run_ads (
  run_id text NOT NULL REFERENCES runs(id),
  ad_id text NOT NULL REFERENCES ads(id),
  state text NOT NULL,            -- queued|cached|completed|needs_review|escalated|analysis_failed|budget_paused
  error_code text,
  PRIMARY KEY (run_id, ad_id)
);

CREATE TABLE IF NOT EXISTS ad_metrics (
  ad_id text NOT NULL REFERENCES ads(id),
  window text NOT NULL,           -- lifetime | last_30d | last_7d
  country text NOT NULL,          -- insights broken down by country; reports use US
  date_start text, date_stop text,
  spend real, impressions integer, reach integer, clicks integer, link_clicks integer,
  ctr real, cpm real, cpc real, frequency real,
  meta_landing_page_views integer, meta_add_to_cart integer,
  meta_initiate_checkout integer, meta_purchases integer, meta_leads integer,
  actions text NOT NULL DEFAULT '[]',
  fetched_at text NOT NULL,
  PRIMARY KEY (ad_id, window, country)
);

CREATE TABLE IF NOT EXISTS media_assets (
  id text PRIMARY KEY,
  ad_id text NOT NULL REFERENCES ads(id),
  kind text NOT NULL,             -- image | video | thumbnail
  source_ref text,                -- image hash / video id
  source_url text,
  storage_key text, sha256 text, mime_type text, duration_ms integer, phash text,
  rights_status text NOT NULL,    -- own | internal_analysis_only
  processing_status text NOT NULL DEFAULT 'pending',
  metadata text NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS evidence (
  id text PRIMARY KEY,
  ad_id text NOT NULL REFERENCES ads(id),
  origin text NOT NULL,           -- ad_copy|ad_headline|ad_cta|ocr|asr|frame|landing_dom
  text_value text,
  artifact_key text, locator text, confidence real, extractor_version text,
  created_at text NOT NULL
);

CREATE TABLE IF NOT EXISTS landing_snapshots (
  id text PRIMARY KEY,
  canonical_url text NOT NULL, final_url text, fetched_at text NOT NULL,
  status text NOT NULL,           -- ok|blocked|timeout|missing|unsupported|error
  http_status integer, content_hash text, title text, extracted text,
  html_key text, redirect_chain text, error_code text
);

CREATE TABLE IF NOT EXISTS ad_landing_snapshots (
  ad_id text NOT NULL REFERENCES ads(id),
  snapshot_id text NOT NULL REFERENCES landing_snapshots(id),
  run_id text NOT NULL REFERENCES runs(id),
  PRIMARY KEY (ad_id, snapshot_id, run_id)
);

CREATE TABLE IF NOT EXISTS analyses (
  id text PRIMARY KEY,
  run_id text NOT NULL REFERENCES runs(id),
  ad_id text NOT NULL REFERENCES ads(id),
  taxonomy_version text NOT NULL, prompt_version text NOT NULL, model_id text NOT NULL,
  evidence_hash text NOT NULL, status text NOT NULL,
  labels text NOT NULL, offer_detail text, claims text, landing_comparison text,
  confidence text, review_flags text, model_notes text,
  created_at text NOT NULL,
  UNIQUE(ad_id, taxonomy_version, prompt_version, model_id, evidence_hash)
);

CREATE TABLE IF NOT EXISTS model_calls (
  id text PRIMARY KEY,
  run_id text, ad_id text,
  stage text NOT NULL, provider text NOT NULL, model_id text NOT NULL, prompt_version text,
  input_tokens integer, output_tokens integer, cost_usd real, latency_ms integer,
  status text NOT NULL, error_code text, created_at text NOT NULL
);

CREATE TABLE IF NOT EXISTS review_decisions (
  id text PRIMARY KEY,
  analysis_id text NOT NULL REFERENCES analyses(id),
  reviewer_id text NOT NULL, decision text NOT NULL,
  corrected_labels text, note text, created_at text NOT NULL
);

CREATE INDEX IF NOT EXISTS ads_brand_seen_idx ON ads(brand_id, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS evidence_ad_idx ON evidence(ad_id);
CREATE INDEX IF NOT EXISTS analyses_ad_idx ON analyses(ad_id);
CREATE INDEX IF NOT EXISTS landing_url_time_idx ON landing_snapshots(canonical_url, fetched_at DESC);
"""


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    return str(uuid.uuid4())


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Columns added after the first real data landed. CREATE TABLE IF NOT EXISTS won't add them to
# an existing table, so add them here. Each one is idempotent.
MIGRATIONS = [
    ("model_calls", "detail", "text"),        # per-model usage breakdown from the CLI
    ("analyses", "metadata", "text"),         # e.g. analyzed_as: the representative ad of a duplicate group
]


def _migrate(conn):
    for table, col, typ in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    conn.commit()


def dumps(value) -> str:
    # default=str: YAML turns `2026-11-30` into a date object (offer_facts), which would crash here.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def save_raw(key: str, payload) -> str:
    """Write an immutable raw payload under data/raw/. Returns the key stored on the row."""
    target = DATA_DIR / "raw" / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    return f"raw/{key}"


def upsert_brand(conn, *, id, name, kind, domain=None, page_id=None, parent_brand=None):
    conn.execute(
        """INSERT INTO brands (id, name, kind, domain, page_id, parent_brand)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind,
             domain=excluded.domain, page_id=excluded.page_id,
             parent_brand=excluded.parent_brand""",
        (id, name, kind, domain, page_id, parent_brand),
    )


AD_COLUMNS = [
    "brand_id", "source", "source_ad_id", "source_url", "lane", "campaign_id", "campaign_name",
    "adset_id", "adset_name", "ad_name", "active_status", "headline", "body", "description",
    "cta_text", "text_variants", "destination_url_original", "destination_url_canonical",
    "format", "format_from_name", "ad_code", "country", "language", "source_created_at",
    "raw_payload_key", "metadata",
]


def upsert_ad(conn, ad: dict) -> tuple[str, bool]:
    """Insert or refresh an ad. Identity is (source, source_ad_id), so re-importing never
    duplicates — it updates last_seen_at and the latest observed fields. first_seen_at is
    kept from the first observation. Returns (ad_id, created)."""
    ad_id = f"{ad['source']}:{ad['source_ad_id']}"
    ts = now()
    existing = conn.execute("SELECT 1 FROM ads WHERE id=?", (ad_id,)).fetchone()
    values = {c: ad.get(c) for c in AD_COLUMNS}
    for c in ("text_variants", "metadata"):
        if not isinstance(values[c], str):
            values[c] = dumps(values[c] if values[c] is not None else ([] if c == "text_variants" else {}))
    values["active_status"] = values["active_status"] or "unknown"
    values["format"] = values["format"] or "unknown"
    if existing:
        sets = ", ".join(f"{c}=?" for c in AD_COLUMNS)
        conn.execute(
            f"UPDATE ads SET {sets}, last_seen_at=? WHERE id=?",
            [values[c] for c in AD_COLUMNS] + [ts, ad_id],
        )
        return ad_id, False
    cols = ["id", *AD_COLUMNS, "first_seen_at", "last_seen_at"]
    conn.execute(
        f"INSERT INTO ads ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        [ad_id] + [values[c] for c in AD_COLUMNS] + [ts, ts],
    )
    return ad_id, True


def create_run(conn, request: dict) -> str:
    run_id = new_id()
    conn.execute(
        "INSERT INTO runs (id, status, request, created_at, budget_usd) VALUES (?,?,?,?,?)",
        (run_id, "acquiring", dumps(request), now(), request.get("budget_usd")),
    )
    return run_id


def finish_run(conn, run_id: str, status: str, counters: dict):
    conn.execute(
        "UPDATE runs SET status=?, finished_at=?, counters=? WHERE id=?",
        (status, now(), dumps(counters), run_id),
    )
