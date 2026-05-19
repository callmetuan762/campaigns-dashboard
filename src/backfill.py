"""Backfill CLI: replay Meta and/or GA4 ingestion over a historical date range.

Usage:
    python -m src.backfill --source meta|ga4|all --start YYYY-MM-DD --end YYYY-MM-DD
    python -m src.backfill --source all --start 2026-04-01 --end 2026-04-30 --dry-run

Backfill uses suppress_alerts=True (Meta) and skip_cache=True (GA4) to avoid
historical alert spam and GA4 6-hour deduplication blocks.
Idempotency is guaranteed by the existing INSERT ... ON CONFLICT DO UPDATE UPSERTs.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

import structlog

from src.config import load_settings
from src.db.client import DBClient
from src.logging_setup import configure_logging

logger = structlog.get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill Meta/GA4 historical data into the canonical store."
    )
    parser.add_argument(
        "--source",
        choices=["meta", "ga4", "all"],
        required=True,
        help="Data source to backfill",
    )
    parser.add_argument(
        "--start",
        required=True,
        metavar="YYYY-MM-DD",
        help="Start date (inclusive)",
    )
    parser.add_argument(
        "--end",
        required=True,
        metavar="YYYY-MM-DD",
        help="End date (inclusive)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log the date range without writing to the DB",
    )
    return parser.parse_args()


def _date_range(start: date, end: date) -> list[str]:
    """Return ISO-format date strings from start to end inclusive."""
    result: list[str] = []
    current = start
    while current <= end:
        result.append(current.isoformat())
        current += timedelta(days=1)
    return result


async def backfill_main(
    source: str,
    start: date,
    end: date,
    dry_run: bool = False,
) -> None:
    settings = load_settings()
    configure_logging(level=settings.log_level, fmt="json")

    dates = _date_range(start, end)
    log = structlog.get_logger(__name__)

    log.info(
        "backfill_date_start",
        source=source,
        start=start.isoformat(),
        end=end.isoformat(),
        date_count=len(dates),
        dry_run=dry_run,
    )

    if dry_run:
        for d in dates:
            log.info("backfill_date_current", source=source, date=d, dry_run=True)
        log.info("backfill_complete", source=source, dry_run=True, dates_logged=len(dates))
        return

    db = DBClient(settings.db_path)
    await db.connect()

    try:
        if source in ("meta", "all"):
            from src.meta.ingest import run_meta_ingest_for_date
            for d in dates:
                log.info("backfill_date_current", source="meta", date=d)
                await run_meta_ingest_for_date(db, settings, d)

        if source in ("ga4", "all"):
            from src.ga4.ingest import run_ga4_ingest_for_date
            for d in dates:
                log.info("backfill_date_current", source="ga4", date=d)
                await run_ga4_ingest_for_date(db, settings, d)

    finally:
        await db.close()

    log.info(
        "backfill_complete",
        source=source,
        start=start.isoformat(),
        end=end.isoformat(),
        dates_processed=len(dates),
    )


if __name__ == "__main__":
    _args = _parse_args()
    asyncio.run(
        backfill_main(
            source=_args.source,
            start=date.fromisoformat(_args.start),
            end=date.fromisoformat(_args.end),
            dry_run=_args.dry_run,
        )
    )
