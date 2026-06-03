"""
Fetch extra data for report enhancements.
- Device breakdown (desktop vs mobile) from Meta API
- Ad-level performance from DB
- Daily CPR time series from DB
- Weekly CAC from DB

Saves to reports/extra_data.json
Run with: python -X utf8 scripts/fetch_extra_data.py
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "metrics.db"
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
OUTPUT = REPORTS_DIR / "extra_data.json"

# Top 3 campaign IDs
TOP3_IDS = [
    "120243727739260025",  # Nostalgia Bridge Dad
    "120243727242320025",  # Sturdy Parenting
    "120243727123190025",  # Routine-Chaos
]
ID_TO_NAME = {
    "120243727739260025": "Nostalgia Bridge Dad",
    "120243727242320025": "Sturdy Parenting",
    "120243727123190025": "Routine-Chaos",
}
DATE_SINCE = "2026-05-12"
DATE_UNTIL = "2026-05-31"


# ---------------------------------------------------------------------------
# A. Device breakdown from Meta API
# ---------------------------------------------------------------------------
def fetch_device_breakdown():
    """Pull spend/impressions/clicks by device_platform for top 3 campaigns."""
    try:
        from facebook_business.api import FacebookAdsApi
        from facebook_business.adobjects.adaccount import AdAccount
        from src.config import load_settings

        settings = load_settings()
        FacebookAdsApi.init(
            app_id=settings.meta_app_id,
            app_secret=settings.meta_app_secret.get_secret_value(),
            access_token=settings.meta_access_token.get_secret_value(),
            api_version="v24.0",
        )
        account = AdAccount(
            f"act_{settings.meta_ad_account_id.removeprefix('act_')}"
        )
    except Exception as e:
        print(f"  [warn] Meta API init failed: {e}")
        return {}

    device_data = {}
    for campaign_id in TOP3_IDS:
        name = ID_TO_NAME[campaign_id]
        print(f"  Fetching device breakdown for {name} ...")
        try:
            params = {
                "level": "campaign",
                "filtering": [
                    {
                        "field": "campaign.id",
                        "operator": "EQUAL",
                        "value": campaign_id,
                    }
                ],
                "breakdowns": ["device_platform"],
                "time_range": {"since": DATE_SINCE, "until": DATE_UNTIL},
                "limit": 500,
            }
            fields = ["campaign_id", "spend", "impressions", "clicks", "ctr", "cpc"]
            cursor = account.get_insights(fields=fields, params=params)
            rows = []
            while True:
                rows.extend([dict(r) for r in cursor])
                if cursor.load_next_page() is False:
                    break
            device_data[campaign_id] = rows
            print(f"    Got {len(rows)} device rows")
        except Exception as e:
            print(f"  [warn] Device fetch failed for {campaign_id}: {e}")
            device_data[campaign_id] = []

    return device_data


# ---------------------------------------------------------------------------
# B-D. DB queries
# ---------------------------------------------------------------------------
def fetch_db_data():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # B. Ad-level performance
    cur.execute("""
        SELECT
            ac.ad_name, ac.ad_format, ac.ad_style, ac.campaign_id,
            ROUND(SUM(am.spend), 2) as spend,
            SUM(am.impressions) as impressions,
            SUM(am.clicks) as clicks,
            ROUND(AVG(am.ctr), 2) as avg_ctr,
            ROUND(AVG(am.cpc), 3) as avg_cpc,
            SUM(am.meta_form_submit_deposit) as total_fsd
        FROM ad_metrics am
        JOIN ad_creatives ac ON am.ad_id = ac.ad_id
        WHERE am.campaign_id IN ('120243727739260025','120243727242320025','120243727123190025')
          AND am.ad_id != ''
        GROUP BY ac.ad_name, ac.ad_format, ac.ad_style, ac.campaign_id
        ORDER BY total_fsd DESC
    """)
    ad_perf_rows = cur.fetchall()
    ad_perf = [
        {
            "ad_name": r[0],
            "ad_format": r[1],
            "ad_style": r[2],
            "campaign_id": r[3],
            "spend": r[4],
            "impressions": r[5],
            "clicks": r[6],
            "avg_ctr": r[7],
            "avg_cpc": r[8],
            "total_fsd": r[9] or 0,
        }
        for r in ad_perf_rows
    ]

    # C. Daily CPR
    cur.execute("""
        SELECT campaign_id, date, spend, meta_form_submit_deposit,
            CASE WHEN meta_form_submit_deposit > 0
                 THEN ROUND(spend / meta_form_submit_deposit, 2)
                 ELSE NULL END as cpr
        FROM ad_metrics
        WHERE campaign_id IN ('120243727739260025','120243727242320025','120243727123190025')
          AND ad_set_id = ''
        ORDER BY campaign_id, date
    """)
    cpr_rows = cur.fetchall()
    daily_cpr = [
        {
            "campaign_id": r[0],
            "date": r[1],
            "spend": r[2],
            "fsd": r[3],
            "cpr": r[4],
        }
        for r in cpr_rows
    ]

    # D. Weekly CAC
    cur.execute("""
        SELECT campaign_id,
            CASE WHEN date BETWEEN '2026-05-13' AND '2026-05-19' THEN 'Week 1'
                 WHEN date BETWEEN '2026-05-20' AND '2026-05-26' THEN 'Week 2'
                 ELSE 'Week 3' END as week,
            ROUND(SUM(spend), 2) as spend,
            SUM(meta_form_submit_deposit) as fsd
        FROM ad_metrics
        WHERE campaign_id IN ('120243727739260025','120243727242320025','120243727123190025')
          AND ad_set_id = ''
        GROUP BY campaign_id, week
        ORDER BY campaign_id, week
    """)
    weekly_rows = cur.fetchall()
    weekly_cac = [
        {
            "campaign_id": r[0],
            "week": r[1],
            "spend": r[2],
            "fsd": r[3],
        }
        for r in weekly_rows
    ]

    conn.close()
    return ad_perf, daily_cpr, weekly_cac


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Fetching device breakdown from Meta API ...")
    device_data = fetch_device_breakdown()

    print("\nFetching DB data ...")
    ad_perf, daily_cpr, weekly_cac = fetch_db_data()
    print(f"  Ad perf rows: {len(ad_perf)}")
    print(f"  Daily CPR rows: {len(daily_cpr)}")
    print(f"  Weekly CAC rows: {len(weekly_cac)}")

    output = {
        "device_breakdown": device_data,
        "ad_performance": ad_perf,
        "daily_cpr": daily_cpr,
        "weekly_cac": weekly_cac,
    }

    OUTPUT.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nSaved: {OUTPUT}")

    # Print device summary
    if device_data:
        print("\nDevice breakdown summary:")
        for cid, rows in device_data.items():
            name = ID_TO_NAME.get(cid, cid)
            print(f"  {name}:")
            for r in rows:
                print(f"    {r.get('device_platform','?')}: spend=${r.get('spend','?')}, ctr={r.get('ctr','?')}%, cpc=${r.get('cpc','?')}")
