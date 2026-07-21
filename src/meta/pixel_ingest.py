"""Pixel health ingestion: per-event browser/server counts + best-effort EMQ.

Wired into src/daily_backfill.py exactly like the other optional sources
(Shopify/Sheets, funnel-v3): a clean no-op when META_PIXEL_ID is unset, and
never crashes the backfill when the /stats or dataset_quality endpoints error
(CLAUDE.md graceful degradation) — every failure is caught, logged, and the
function returns normally.
"""
from __future__ import annotations

import structlog

from src.meta.client import fetch_pixel_emq, fetch_pixel_event_counts, init_meta_api

logger = structlog.get_logger(__name__)


def _resolve_access_token(settings) -> str | None:
    """Best-effort extraction of a plain-string access token from settings.

    settings.meta_access_token may be a pydantic SecretStr (production Settings)
    or a plain str (dashboard/test settings objects) — support both without
    importing pydantic here.
    """
    token = getattr(settings, "meta_access_token", None)
    if token is None:
        return None
    get_secret = getattr(token, "get_secret_value", None)
    if callable(get_secret):
        return get_secret()
    return str(token)


async def run_pixel_health_ingest_for_date(db, settings, date_iso: str) -> None:
    """Fetch + upsert pixel_health rows for one day. Never raises.

    Skips with a logged "skipped" message when META_PIXEL_ID is unset (graceful
    degradation, matching src/shopify/ingest.py's no-op pattern). Any error from
    the Meta API (stats endpoint down, permission error, etc.) is caught and
    logged rather than propagated, so a single bad day never fails the whole
    daily_backfill_job run.
    """
    pixel_id = getattr(settings, "meta_pixel_id", None)
    if not pixel_id:
        logger.info("pixel_health_ingest_skipped", reason="META_PIXEL_ID not configured")
        return

    try:
        init_meta_api(settings)

        counts = await fetch_pixel_event_counts(pixel_id, date_iso)
        if not counts:
            logger.info("pixel_health_no_events", date=date_iso)
            return

        emq_map: dict[str, dict[str, float | None]] = {}
        token_value = _resolve_access_token(settings)
        if token_value:
            emq_map = await fetch_pixel_emq(pixel_id, token_value)

        rows = []
        for event_name, event_counts in counts.items():
            extra = emq_map.get(event_name, {})
            rows.append(
                {
                    "date": date_iso,
                    "event_name": event_name,
                    "browser_count": event_counts.get("browser_count", 0),
                    "server_count": event_counts.get("server_count", 0),
                    "dedup_rate": extra.get("dedup_rate"),
                    "emq_score": extra.get("emq_score"),
                }
            )

        upserted = await db.upsert_pixel_health(rows)
        logger.info("pixel_health_ingest_complete", date=date_iso, rows=upserted)

    except Exception as exc:  # noqa: BLE001 — never crash the backfill (CLAUDE.md)
        logger.error("pixel_health_ingest_failed", date=date_iso, error=str(exc))
