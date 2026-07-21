"""Async aiosqlite client wrapping migrations and UPSERT helpers.

INFRA-03: All writes go through UPSERT helpers using INSERT ... ON CONFLICT DO UPDATE,
making re-runs idempotent at the SQL layer (no Python-side read-modify-write).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite
import structlog

from src.db.migrations import run_migrations

if TYPE_CHECKING:
    from src.mmm.model import MMMResult

logger = structlog.get_logger(__name__)


def _is_tool_result_turn(msg: dict) -> bool:
    """Return True if this is a user turn whose content consists entirely of tool_result blocks."""
    content = msg.get("content")
    if not isinstance(content, list):
        return False
    return all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def _is_tool_use_turn(msg: dict) -> bool:
    """Return True if this assistant turn contains at least one tool_use block."""
    content = msg.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)


def _sanitize_history(messages: list[dict]) -> list[dict]:
    """Remove tool_result / tool_use turns that would cause a 400 from Anthropic.

    Invariant: a user turn with tool_result blocks must be immediately preceded by
    an assistant turn with tool_use blocks. Violations occur when the history window
    cuts a conversation mid-loop or a crash left orphaned turns.
    """
    if not messages:
        return messages
    # Drop leading user turns that are pure tool_result (no preceding tool_use)
    while messages and _is_tool_result_turn(messages[0]):
        messages = messages[1:]
    # Drop trailing assistant turns that still have tool_use (no following tool_result)
    while messages and messages[-1].get("role") == "assistant" and _is_tool_use_turn(messages[-1]):
        messages = messages[:-1]
    # Drop any adjacent pair where a tool_result user turn follows a non-tool-use assistant turn
    clean: list[dict] = []
    for msg in messages:
        if _is_tool_result_turn(msg) and (not clean or not _is_tool_use_turn(clean[-1])):
            continue  # orphaned tool_result — drop it
        clean.append(msg)
    return clean


def _deserialize_message(role: str, raw_message: str) -> dict:
    """Convert a stored bot_conversations row into an Anthropic messages-API dict.

    D-08: message stored as plain string for text turns, JSON-encoded list for
    tool_use / tool_result turns. Falls back to raw string on JSONDecodeError.
    Role 'tool' is remapped to 'user' for Anthropic API (tool_result lives inside user turns).
    """
    try:
        content = json.loads(raw_message)
        if isinstance(content, str):
            # json.loads of a quoted string yields a string — treat as plain text
            content = raw_message
    except (json.JSONDecodeError, ValueError):
        content = raw_message

    api_role = "user" if role == "tool" else role
    return {"role": api_role, "content": content}


class DBClient:
    """Thin async wrapper over aiosqlite with typed UPSERT helpers."""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("DBClient.connect() not called")
        return self._conn

    async def connect(self) -> None:
        """Open the connection, set PRAGMAs, and apply migrations."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.execute("PRAGMA busy_timeout=5000;")
        await self._conn.commit()
        applied = await run_migrations(self._conn)
        logger.info("db_connected", path=str(self._path), migrations_applied=applied)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def execute(self, sql: str, params: dict | tuple | None = None) -> None:
        await self.conn.execute(sql, params or ())
        await self.conn.commit()

    async def fetch_one(self, sql: str, params: dict | tuple | None = None) -> dict | None:
        async with self.conn.execute(sql, params or ()) as cur:
            row = await cur.fetchone()
            return dict(row) if row is not None else None

    async def fetch_all(self, sql: str, params: dict | tuple | None = None) -> list[dict]:
        async with self.conn.execute(sql, params or ()) as cur:
            return [dict(r) async for r in cur]

    # ---- UPSERT helpers ----

    _UPSERT_AD_METRICS_SQL = """
        INSERT INTO ad_metrics (
            campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, ctr, cpc, cpm, roas,
            meta_purchases_7dclick, meta_cost_per_purchase, reach, frequency,
            meta_form_submit_deposit,
            landing_page_views, video_3s_views, video_thruplay,
            meta_begin_checkout, meta_cost_per_begin_checkout, meta_add_to_cart, meta_leads
        ) VALUES (
            :campaign_id, :date, :ad_set_id, :ad_id, :spend, :impressions, :clicks, :ctr, :cpc, :cpm, :roas,
            :meta_purchases_7dclick, :meta_cost_per_purchase, :reach, :frequency,
            :meta_form_submit_deposit,
            :landing_page_views, :video_3s_views, :video_thruplay,
            :meta_begin_checkout, :meta_cost_per_begin_checkout, :meta_add_to_cart, :meta_leads
        )
        ON CONFLICT(campaign_id, date, ad_set_id, ad_id) DO UPDATE SET
            spend                         = excluded.spend,
            impressions                   = excluded.impressions,
            clicks                        = excluded.clicks,
            ctr                           = excluded.ctr,
            cpc                           = excluded.cpc,
            cpm                           = excluded.cpm,
            roas                          = excluded.roas,
            meta_purchases_7dclick        = excluded.meta_purchases_7dclick,
            meta_cost_per_purchase        = excluded.meta_cost_per_purchase,
            reach                         = excluded.reach,
            frequency                     = excluded.frequency,
            meta_form_submit_deposit      = excluded.meta_form_submit_deposit,
            landing_page_views            = excluded.landing_page_views,
            video_3s_views                = excluded.video_3s_views,
            video_thruplay                = excluded.video_thruplay,
            meta_begin_checkout           = excluded.meta_begin_checkout,
            meta_cost_per_begin_checkout  = excluded.meta_cost_per_begin_checkout,
            meta_add_to_cart              = excluded.meta_add_to_cart,
            meta_leads                    = excluded.meta_leads,
            fetched_at                    = datetime('now');
    """

    # funnel-v3: columns added by MIGRATION_011_META_FUNNEL_V3. Defaulted to None here
    # (merged UNDER each row, so any key the caller supplies wins) so that callers built
    # before funnel-v3 — hand-built dicts in older tests, dashboard code paths that only
    # know the Phase 1/2 shape — keep working without every one of them being updated to
    # know about the new columns (CLAUDE.md graceful degradation: missing == NULL).
    _AD_METRICS_FUNNEL_V3_DEFAULTS: dict = {
        "landing_page_views": None,
        "video_3s_views": None,
        "video_thruplay": None,
        "meta_begin_checkout": None,
        "meta_cost_per_begin_checkout": None,
        "meta_add_to_cart": None,
        "meta_leads": None,
    }

    _UPSERT_GA4_METRICS_SQL = """
        INSERT INTO ga4_metrics (
            campaign_utm, date, sessions, users, new_users, bounce_rate,
            avg_engagement_time, ga4_purchases_lastclick
        ) VALUES (
            :campaign_utm, :date, :sessions, :users, :new_users, :bounce_rate,
            :avg_engagement_time, :ga4_purchases_lastclick
        )
        ON CONFLICT(campaign_utm, date) DO UPDATE SET
            sessions                = excluded.sessions,
            users                   = excluded.users,
            new_users               = excluded.new_users,
            bounce_rate             = excluded.bounce_rate,
            avg_engagement_time     = excluded.avg_engagement_time,
            ga4_purchases_lastclick = excluded.ga4_purchases_lastclick,
            fetched_at              = datetime('now');
    """

    async def upsert_ad_metrics(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        normalized = [{**self._AD_METRICS_FUNNEL_V3_DEFAULTS, **r} for r in rows]
        await self.conn.executemany(self._UPSERT_AD_METRICS_SQL, normalized)
        await self.conn.commit()
        return len(rows)

    async def upsert_ga4_metrics(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        await self.conn.executemany(self._UPSERT_GA4_METRICS_SQL, rows)
        await self.conn.commit()
        return len(rows)

    _UPSERT_GA4_LANDING_PAGES_SQL = """
        INSERT INTO ga4_landing_pages (
            landing_page, date, sessions, total_users,
            ga4_purchases_lastclick, screen_page_views, avg_engagement_time
        ) VALUES (
            :landing_page, :date, :sessions, :total_users,
            :ga4_purchases_lastclick, :screen_page_views, :avg_engagement_time
        )
        ON CONFLICT(landing_page, date) DO UPDATE SET
            sessions                = excluded.sessions,
            total_users             = excluded.total_users,
            ga4_purchases_lastclick = excluded.ga4_purchases_lastclick,
            screen_page_views       = excluded.screen_page_views,
            avg_engagement_time     = excluded.avg_engagement_time,
            fetched_at              = datetime('now');
    """

    async def upsert_ga4_landing_pages(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        await self.conn.executemany(self._UPSERT_GA4_LANDING_PAGES_SQL, rows)
        await self.conn.commit()
        return len(rows)

    # ---- Funnel v3: GA4 event-level metrics ----

    _UPSERT_GA4_EVENTS_SQL = """
        INSERT INTO ga4_events (
            event_name, date, campaign_utm, lp_slug, event_count
        ) VALUES (
            :event_name, :date, :campaign_utm, :lp_slug, :event_count
        )
        ON CONFLICT(event_name, date, campaign_utm, lp_slug) DO UPDATE SET
            event_count = excluded.event_count,
            fetched_at  = datetime('now');
    """

    async def upsert_ga4_events(self, rows: list[dict]) -> int:
        """Upsert ga4_events rows. Idempotent via PK (event_name, date, campaign_utm, lp_slug)."""
        if not rows:
            return 0
        await self.conn.executemany(self._UPSERT_GA4_EVENTS_SQL, rows)
        await self.conn.commit()
        return len(rows)

    # ---- Phase 2 helpers ----

    _UPSERT_CAMPAIGN_SQL = """
        INSERT INTO campaigns (id, source, name, status)
        VALUES (:id, :source, :name, :status)
        ON CONFLICT(id) DO UPDATE SET
            name   = excluded.name,
            status = excluded.status;
    """

    async def upsert_campaign(self, rows: list[dict]) -> int:
        """Upsert campaign dimension rows. Returns count of rows processed."""
        if not rows:
            return 0
        await self.conn.executemany(self._UPSERT_CAMPAIGN_SQL, rows)
        await self.conn.commit()
        return len(rows)

    _INSERT_INGESTION_LOG_SQL = """
        INSERT INTO ingestion_log (source, started_at, status, rows_upserted)
        VALUES (:source, datetime('now'), 'running', 0)
    """

    _FINISH_INGESTION_LOG_SQL = """
        UPDATE ingestion_log
        SET finished_at   = datetime('now'),
            status        = :status,
            rows_upserted = :rows_upserted,
            error_message = :error_message
        WHERE id = :id
    """

    async def log_ingestion_start(self, source: str) -> int:
        """Insert a 'running' ingestion_log row. Returns the new row id."""
        async with self.conn.execute(
            self._INSERT_INGESTION_LOG_SQL, {"source": source}
        ) as cur:
            await self.conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    async def log_ingestion_finish(
        self,
        log_id: int,
        status: str,
        rows_upserted: int = 0,
        error: str | None = None,
    ) -> None:
        """Update an ingestion_log row to success/failed/partial."""
        await self.conn.execute(
            self._FINISH_INGESTION_LOG_SQL,
            {
                "id": log_id,
                "status": status,
                "rows_upserted": rows_upserted,
                "error_message": error,
            },
        )
        await self.conn.commit()

    _INSERT_ALERT_LOG_SQL = """
        INSERT OR IGNORE INTO alert_log (alert_type, campaign_id, date, fired_at)
        VALUES (:alert_type, :campaign_id, :date, datetime('now'))
    """

    async def log_alert(self, alert_type: str, campaign_id: str, date: str) -> bool:
        """Insert into alert_log with deduplication. Returns True if newly fired, False if duplicate.

        D-18: UNIQUE(alert_type, campaign_id, date) constraint prevents re-alerting per day.
        """
        async with self.conn.execute(
            self._INSERT_ALERT_LOG_SQL,
            {"alert_type": alert_type, "campaign_id": campaign_id, "date": date},
        ) as cur:
            await self.conn.commit()
            return cur.rowcount == 1

    # ---- helpers used by /status handler (Plan 03) ----

    _COUNT_TABLES: frozenset[str] = frozenset(
        {"campaigns", "ad_metrics", "ga4_metrics", "bot_conversations"}
    )

    async def get_row_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in ("campaigns", "ad_metrics", "ga4_metrics", "bot_conversations"):
            if table not in self._COUNT_TABLES:
                raise ValueError(f"Table {table!r} not in allowlist")
            row = await self.fetch_one(f"SELECT COUNT(*) AS n FROM {table}")  # noqa: S608
            counts[table] = int(row["n"]) if row else 0
        return counts

    async def get_last_sync(self) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        meta = await self.fetch_one("SELECT MAX(fetched_at) AS t FROM ad_metrics")
        ga4 = await self.fetch_one("SELECT MAX(fetched_at) AS t FROM ga4_metrics")
        out["meta_ads"] = meta["t"] if meta else None
        out["ga4"] = ga4["t"] if ga4 else None
        return out

    # ---- Phase 4: Anthropic usage + conversation persistence ----

    _INSERT_USAGE_LOG_SQL = """
        INSERT INTO anthropic_usage_log
            (model, input_tokens, output_tokens, cost_usd, chat_id, user_id)
        VALUES
            (:model, :input_tokens, :output_tokens, :cost_usd, :chat_id, :user_id)
    """

    async def log_anthropic_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        chat_id: int | None = None,
        user_id: int | None = None,
    ) -> None:
        """Insert a row into anthropic_usage_log. D-03."""
        await self.conn.execute(
            self._INSERT_USAGE_LOG_SQL,
            {
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
                "chat_id": chat_id,
                "user_id": user_id,
            },
        )
        await self.conn.commit()

    _MONTHLY_COST_SQL = """
        SELECT COALESCE(SUM(cost_usd), 0.0) AS total
        FROM anthropic_usage_log
        WHERE request_at >= datetime('now', 'start of month')
    """

    async def get_monthly_anthropic_cost(self) -> float:
        """Return total cost_usd this calendar month. D-04 budget gate uses this."""
        row = await self.fetch_one(self._MONTHLY_COST_SQL)
        return float(row["total"]) if row else 0.0

    _GET_CONV_HISTORY_SQL = """
        SELECT role, message
        FROM bot_conversations
        WHERE chat_id = :chat_id AND user_id = :user_id
        ORDER BY id DESC
        LIMIT :limit
    """

    async def get_conversation_history(
        self, chat_id: int, user_id: int, limit: int = 10
    ) -> list[dict]:
        """Return last `limit` turns in chronological order as Anthropic-API dicts.

        D-06: scoped to (chat_id, user_id) — each user has an independent thread.
        D-07: limit=10 rows by default; older turns are dropped from context but
        remain in SQLite for auditing.
        D-08: rows whose message is JSON-encoded content list are parsed; plain
        strings are used verbatim. role='tool' rows are remapped to role='user'.
        """
        rows = await self.fetch_all(
            self._GET_CONV_HISTORY_SQL,
            {"chat_id": chat_id, "user_id": user_id, "limit": limit},
        )
        rows.reverse()  # oldest first for Anthropic messages array
        messages = [_deserialize_message(r["role"], r["message"]) for r in rows]
        return _sanitize_history(messages)

    _INSERT_CONV_SQL = """
        INSERT INTO bot_conversations (chat_id, user_id, role, message)
        VALUES (:chat_id, :user_id, :role, :message)
    """

    async def save_conversation_turn(
        self, chat_id: int, user_id: int, role: str, message: str
    ) -> None:
        """Insert one row. Caller is responsible for json.dumps when message is non-text."""
        await self.conn.execute(
            self._INSERT_CONV_SQL,
            {"chat_id": chat_id, "user_id": user_id, "role": role, "message": message},
        )
        await self.conn.commit()

    _CLEAR_CONV_SQL = """
        DELETE FROM bot_conversations
        WHERE chat_id = :chat_id AND user_id = :user_id
    """

    async def clear_conversation(self, chat_id: int, user_id: int) -> None:
        """Remove all conversation rows for (chat_id, user_id). D-09 /clear command."""
        await self.execute(
            self._CLEAR_CONV_SQL,
            {"chat_id": chat_id, "user_id": user_id},
        )

    # ---- Phase 8 (changelog): ad change history ----

    async def upsert_changelog_entries(self, entries: list[dict]) -> int:
        """Insert changelog entries, ignoring duplicates (UNIQUE on object_id+change_time+event_type)."""
        if not entries:
            return 0
        sql = """
            INSERT OR IGNORE INTO ad_changelogs
                (change_time, object_id, object_name, object_type, event_type,
                 changed_fields, old_value, new_value, actor_name)
            VALUES
                (:change_time, :object_id, :object_name, :object_type, :event_type,
                 :changed_fields, :old_value, :new_value, :actor_name)
        """
        async with aiosqlite.connect(str(self._path)) as con:
            await con.executemany(sql, entries)
            await con.commit()
        return len(entries)

    # ---- Phase 8: MMM results (append-only) ----

    _INSERT_MMM_RESULT_SQL = """
        INSERT INTO mmm_results (
            run_date, weeks_of_data, media_pct, baseline_pct,
            incremental_roas_per_1k, optimal_daily_spend,
            theta, km, n, maturity_label
        ) VALUES (
            :run_date, :weeks_of_data, :media_pct, :baseline_pct,
            :incremental_roas_per_1k, :optimal_daily_spend,
            :theta, :km, :n, :maturity_label
        )
    """

    async def upsert_mmm_result(self, result: "MMMResult") -> None:
        """Insert one row into mmm_results from a MMMResult dataclass.

        Append-only — no ON CONFLICT clause. Each weekly run produces a new row;
        the dashboard reads ORDER BY run_date DESC LIMIT 1.
        """
        await self.conn.execute(self._INSERT_MMM_RESULT_SQL, result.to_dict())
        await self.conn.commit()

    _SELECT_MMM_RESULTS_SQL = """
        SELECT * FROM mmm_results ORDER BY run_date DESC LIMIT ?
    """

    async def get_mmm_results(self, limit: int = 10) -> list[dict]:
        """Return mmm_results rows ordered by run_date DESC, capped at `limit`."""
        return await self.fetch_all(self._SELECT_MMM_RESULTS_SQL, (limit,))

    # ---- Stripe payments (Google Sheets pull) ----

    _UPSERT_STRIPE_PAYMENTS_SQL = """
        INSERT INTO stripe_payments
            (uid, submitted_at, email, source, status, session_id, fetched_at)
        VALUES
            (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(uid) DO UPDATE SET
            status     = excluded.status,
            session_id = excluded.session_id,
            fetched_at = excluded.fetched_at
    """

    async def upsert_ad_creatives(self, rows: list[dict]) -> int:
        """Insert or update ad creative metadata rows."""
        if not rows:
            return 0
        sql = """
            INSERT INTO ad_creatives
                (ad_id, ad_name, adset_id, campaign_id, effective_status,
                 ad_format, ad_style, thumbnail_url, destination_url, preview_url, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(ad_id) DO UPDATE SET
                ad_name=excluded.ad_name,
                effective_status=excluded.effective_status,
                ad_format=excluded.ad_format,
                ad_style=excluded.ad_style,
                thumbnail_url=excluded.thumbnail_url,
                destination_url=excluded.destination_url,
                preview_url=excluded.preview_url,
                fetched_at=excluded.fetched_at
        """
        async with self._conn.cursor() as cur:
            for row in rows:
                await cur.execute(sql, (
                    row['ad_id'], row['ad_name'], row.get('adset_id', ''),
                    row.get('campaign_id', ''), row.get('effective_status', ''),
                    row.get('ad_format', ''), row.get('ad_style', ''),
                    row.get('thumbnail_url', ''), row.get('destination_url', ''),
                    row.get('preview_url', ''),
                ))
            await self._conn.commit()
        return len(rows)

    async def upsert_stripe_payments(self, rows: list[dict]) -> int:
        """Upsert stripe_payments rows.

        Updates status and session_id if uid already exists (pending can become paid).
        Returns count of rows processed.

        INFRA-03: idempotent at SQL layer via INSERT ... ON CONFLICT DO UPDATE.
        """
        if not rows:
            return 0
        params = [
            (
                r["uid"],
                r["submitted_at"],
                r.get("email"),
                r.get("source"),
                r["status"],
                r.get("session_id"),
            )
            for r in rows
        ]
        await self.conn.executemany(self._UPSERT_STRIPE_PAYMENTS_SQL, params)
        await self.conn.commit()
        return len(rows)

    # ---- Funnel v3: Shopify orders ----

    _UPSERT_SHOPIFY_ORDERS_SQL = """
        INSERT INTO shopify_orders (
            order_id, created_at, order_date, total_price, financial_status,
            utm_source, utm_campaign, utm_content, lp_slug, landing_site, referring_site
        ) VALUES (
            :order_id, :created_at, :order_date, :total_price, :financial_status,
            :utm_source, :utm_campaign, :utm_content, :lp_slug, :landing_site, :referring_site
        )
        ON CONFLICT(order_id) DO UPDATE SET
            created_at       = excluded.created_at,
            order_date       = excluded.order_date,
            total_price      = excluded.total_price,
            financial_status = excluded.financial_status,
            utm_source       = excluded.utm_source,
            utm_campaign     = excluded.utm_campaign,
            utm_content      = excluded.utm_content,
            lp_slug          = excluded.lp_slug,
            landing_site     = excluded.landing_site,
            referring_site   = excluded.referring_site,
            fetched_at       = datetime('now');
    """

    async def upsert_shopify_orders(self, rows: list[dict]) -> int:
        """Upsert shopify_orders rows. Idempotent via PK (order_id).

        INFRA-03: idempotent at SQL layer via INSERT ... ON CONFLICT DO UPDATE, mirroring
        upsert_stripe_payments — financial_status can transition (e.g. pending -> paid)
        on re-ingest of the same order.
        """
        if not rows:
            return 0
        await self.conn.executemany(self._UPSERT_SHOPIFY_ORDERS_SQL, rows)
        await self.conn.commit()
        return len(rows)
