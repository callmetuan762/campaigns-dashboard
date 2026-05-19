"""Async aiosqlite client wrapping migrations and UPSERT helpers.

INFRA-03: All writes go through UPSERT helpers using INSERT ... ON CONFLICT DO UPDATE,
making re-runs idempotent at the SQL layer (no Python-side read-modify-write).
"""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import structlog

from src.db.migrations import run_migrations

logger = structlog.get_logger(__name__)


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
            meta_purchases_7dclick, meta_cost_per_purchase, reach, frequency
        ) VALUES (
            :campaign_id, :date, :ad_set_id, :ad_id, :spend, :impressions, :clicks, :ctr, :cpc, :cpm, :roas,
            :meta_purchases_7dclick, :meta_cost_per_purchase, :reach, :frequency
        )
        ON CONFLICT(campaign_id, date, ad_set_id, ad_id) DO UPDATE SET
            spend                  = excluded.spend,
            impressions            = excluded.impressions,
            clicks                 = excluded.clicks,
            ctr                    = excluded.ctr,
            cpc                    = excluded.cpc,
            cpm                    = excluded.cpm,
            roas                   = excluded.roas,
            meta_purchases_7dclick = excluded.meta_purchases_7dclick,
            meta_cost_per_purchase = excluded.meta_cost_per_purchase,
            reach                  = excluded.reach,
            frequency              = excluded.frequency,
            fetched_at             = datetime('now');
    """

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
        await self.conn.executemany(self._UPSERT_AD_METRICS_SQL, rows)
        await self.conn.commit()
        return len(rows)

    async def upsert_ga4_metrics(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        await self.conn.executemany(self._UPSERT_GA4_METRICS_SQL, rows)
        await self.conn.commit()
        return len(rows)

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
