"""Backfill script: fetch ad creative metadata and ad-level insights.

Usage:
    python scripts/backfill_ad_creatives.py

Fetches:
  - Ad creatives (thumbnail, style, format, URLs) for all ACTIVE/PAUSED ads
  - Ad-level insights for the last 7 days

Requires .env with META_AD_ACCOUNT_ID, META_APP_ID, META_APP_SECRET, META_ACCESS_TOKEN.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

from src.config import load_settings
from src.db.client import DBClient
from src.meta.client import fetch_ad_creatives, fetch_ad_insights, init_meta_api


async def run() -> None:
    settings = load_settings()
    db = DBClient(settings.db_path)
    await db.connect()
    init_meta_api(settings)

    print("Fetching ad creatives...")
    creatives = await fetch_ad_creatives(settings.meta_ad_account_id)
    if creatives:
        n = await db.upsert_ad_creatives(creatives)
        print(f"Upserted {n} ad creatives")
    else:
        print("No active/paused ads found.")

    print("Fetching ad-level insights for last 7 days...")
    for i in range(1, 8):
        d = (date.today() - timedelta(days=i)).isoformat()
        rows = await fetch_ad_insights(settings.meta_ad_account_id, d)
        if rows:
            n = await db.upsert_ad_metrics(rows)
            print(f"  {d}: {n} ad-level rows upserted")
        else:
            print(f"  {d}: no data")

    await db.close()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(run())
