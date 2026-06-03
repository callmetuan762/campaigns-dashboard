"""
Build investor and retrospective HTML reports for Nowa Meta Ads campaign testing.
May 12–31, 2026. Run with: python -X utf8 scripts/build_reports.py

Sections:
  Investor Report:
    1. The Experiment
    2. Campaign Rankings — All 10
    3. The Funnel — Top 3 Winners
    4. Why These 3 Won  (enhanced with first-touchpoint lines)
    5. CAC Trend (Week 1 → Week 3)
    6. Investment Summary

  Retro Report:
    1. All 10 Audience Segments — Full Metrics  (segment-based)
    2. Elimination Timeline
    3. Weekly Performance Trend  (segment totals per week)
    4. Stripe Deposits by Segment
    5. Key Decisions Log
    6. Customer Profile per Segment
    7. Ad Format & Angle Verdict
    8. Desktop vs Mobile
    9. Time Series Anomalies  (segment-level daily CPR)
   10. Audience Exclusion Plan
   11. Scale-up Recommendations  (segment CPaC numbers)
"""
import csv
import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

# Make src.config importable
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "metrics.db"
CSV_PATH = Path(r"C:\Users\unive\Desktop\draf\Nowa Deposit $1 - Payments-20260529-1011.csv")
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
EXTRA_DATA_PATH = REPORTS_DIR / "extra_data.json"

# ---------------------------------------------------------------------------
# CAMPAIGN DATA (exact numbers from ad_metrics, campaign-level rows)
# ---------------------------------------------------------------------------
CAMPAIGNS = [
    # name, spend, impr, clicks, ctr, cpc, cpm, total_fsd, days, date_range
    # Values from DB after full backfill (incl. May 29-30 recovery). Stripe from DB table.
    ("Nostalgia Bridge Dad", 831.66, 35502, 3844, 11.30, 0.241, 27.18, 67, 19, "May 13–31"),
    ("Sturdy Parenting",     799.82, 22828, 2192, 10.38, 0.419, 42.24, 60, 19, "May 13–31"),
    ("Routine-Chaos",        793.92, 28963, 1908,  6.87, 0.493, 33.26, 42, 19, "May 13–31"),
    ("Anxiety Regulation",   576.93, 19249, 1304,  6.72, 0.590, 38.63, 32, 15, "May 13–27"),
    ("ADHD-EF Intervention", 586.17, 17693, 1122,  7.05, 0.591, 40.88, 21, 15, "May 13–27"),
    ("iPad Battle Mom",      320.47,  9717,  922,  9.88, 0.416, 40.12, 17, 10, "May 13–22"),
    ("Selective Mutism",     310.00, 10147,  695,  7.31, 0.496, 36.19, 19, 10, "May 13–22"),
    ("Homework Meltdown",    299.85, 10833,  607,  6.25, 0.544, 34.07, 20, 10, "May 13–22"),
    ("AI-Curious Parent",    160.97,  3017,  186,  5.09, 0.972, 52.56,  2,  7, "May 13–19"),
    ("Homeschool",           151.98,  3692,  313,  8.71, 0.525, 44.43,  8,  6, "May 13–18"),
]

# Source → campaign name mapping
SOURCE_TO_CAMPAIGN = {
    "1a-screen-time":         "iPad Battle Mom",
    "1b-homework-meltdown":   "Homework Meltdown",
    "1c-anxiety-regulation":  "Anxiety Regulation",
    "1d-routine-chaos":       "Routine-Chaos",
    "2b-sturdy-parenting":    "Sturdy Parenting",
    "2c-homeschool":          "Homeschool",
    "3a-first-ai-introduction": "AI-Curious Parent",
    "5a-pcit-at-home":        "ADHD-EF Intervention",
    "5b-pcit-sm-at-home":     "Selective Mutism",
    "6a-nostalgia-bridge":    "Nostalgia Bridge Dad",
}

# Campaign accent colors
CAMPAIGN_COLORS = {
    "Nostalgia Bridge Dad": "#f59e0b",
    "Sturdy Parenting":     "#34d399",
    "Routine-Chaos":        "#f472b6",
    "Anxiety Regulation":   "#60a5fa",
    "ADHD-EF Intervention": "#818cf8",
    "iPad Battle Mom":      "#94a3b8",
    "Selective Mutism":     "#94a3b8",
    "Homework Meltdown":    "#94a3b8",
    "AI-Curious Parent":    "#64748b",
    "Homeschool":           "#64748b",
}

ID_TO_NAME = {
    "120243727739260025": "Nostalgia Bridge Dad",
    "120243727242320025": "Sturdy Parenting",
    "120243727123190025": "Routine-Chaos",
}
NAME_TO_ID = {v: k for k, v in ID_TO_NAME.items()}


# ---------------------------------------------------------------------------
# SEGMENT CONSTANTS (mirrors segment_report.py)
# ---------------------------------------------------------------------------
SEGMENT_SINCE = "2026-05-11"
SEGMENT_UNTIL = "2026-05-31"

ALL_CAMPAIGNS_FOR_SEGMENTS: dict[str, str] = {
    "120243727739260025": "SALES | Nostalgia Bridge Dad",
    "120243727242320025": "SALES | Sturdy Parenting",
    "120243727123190025": "SALES | Routine-Chaos",
    "120243726950430025": "SALES | Anxiety Regulation",
    "120243727522590025": "SALES | ADHD-EF Intervention",
    "120243717547910025": "SALES | iPad Battle Mom",
    "120243727567700025": "SALES | Selective Mutism",
    "120243717918140025": "SALES | Homework Meltdown",
    "120243727427170025": "SALES | AI-Curious Parent",
    "120243727324310025": "SALES | Homeschool",
    "120244568399160025": "LEADS | Top Static Ads",
    "120244568479490025": "LEADS | Top Video Ads",
    "120244490348810025": "LEADS | Top 3 PC",
}

SEGMENT_MAP: dict[str, list[str]] = {
    "Nostalgia Bridge Dad": ["NOSTBRD", "Nostalgia"],
    "Sturdy Parenting":     ["STURDY", "Sturdy"],
    "Routine-Chaos":        ["ROUTINE", "Routine"],
    "Homework Meltdown":    ["HOMEWORK", "Homework"],
    "Anxiety Regulation":   ["ANXIETY", "Anxiety"],
    "ADHD-EF Intervention": ["ADHDEF"],
    "iPad Battle Mom":      ["IPADMOM"],
    "Selective Mutism":     ["SELMUT", "Selective"],
    "AI-Curious Parent":    ["AICURIOUS", "AI-Curious"],
    "Homeschool":           ["HOMESCHOOL"],
}

STRIPE_SOURCE_MAP_SEG: dict[str, str] = {
    "6a-nostalgia-bridge":      "Nostalgia Bridge Dad",
    "2b-sturdy-parenting":      "Sturdy Parenting",
    "1d-routine-chaos":         "Routine-Chaos",
    "1c-anxiety-regulation":    "Anxiety Regulation",
    "5a-pcit-at-home":          "ADHD-EF Intervention",
    "1a-screen-time":           "iPad Battle Mom",
    "5b-pcit-sm-at-home":       "Selective Mutism",
    "1b-homework-meltdown":     "Homework Meltdown",
    "3a-first-ai-introduction": "AI-Curious Parent",
    "2c-homeschool":            "Homeschool",
}

# Segment display colours
SEGMENT_COLORS: dict[str, str] = {
    "Nostalgia Bridge Dad": "#f59e0b",
    "Sturdy Parenting":     "#34d399",
    "Routine-Chaos":        "#f472b6",
    "Homework Meltdown":    "#60a5fa",
    "Anxiety Regulation":   "#818cf8",
    "ADHD-EF Intervention": "#a78bfa",
    "iPad Battle Mom":      "#94a3b8",
    "Selective Mutism":     "#64748b",
    "AI-Curious Parent":    "#38bdf8",
    "Homeschool":           "#34d399",
}

# ---------------------------------------------------------------------------
# SEGMENT API HELPERS
# ---------------------------------------------------------------------------

def _seg_init_api(settings) -> None:
    from facebook_business.api import FacebookAdsApi
    FacebookAdsApi.init(
        app_id=settings.meta_app_id,
        app_secret=settings.meta_app_secret.get_secret_value(),
        access_token=settings.meta_access_token.get_secret_value(),
        api_version="v24.0",
    )


def _seg_paginate(cursor) -> list[dict]:
    rows: list[dict] = []
    while True:
        rows.extend([dict(r) for r in cursor])
        if cursor.load_next_page() is False:
            break
    return rows


def _seg_extract_fsd(conversions: list | None) -> float:
    if not conversions:
        return 0.0
    for item in conversions:
        if "form_submit_deposit" in item.get("action_type", ""):
            return float(item.get("value", 0) or 0)
    return 0.0


def _seg_classify(adset_name: str) -> str | None:
    name_upper = adset_name.upper()
    for segment, keywords in SEGMENT_MAP.items():
        for kw in keywords:
            if kw.upper() in name_upper:
                return segment
    return None


def _seg_fetch_adset_insights(
    settings,
    campaign_id: str,
    campaign_label: str,
    time_range: dict,
) -> list[dict]:
    from facebook_business.adobjects.adaccount import AdAccount
    account = AdAccount(f"act_{settings.meta_ad_account_id.removeprefix('act_')}")
    fields = [
        "adset_id", "adset_name", "spend", "impressions",
        "inline_link_clicks", "inline_link_click_ctr", "cpm", "reach", "conversions",
    ]
    params = {
        "level": "adset",
        "filtering": [{"field": "campaign.id", "operator": "EQUAL", "value": campaign_id}],
        "time_range": time_range,
        "limit": 500,
    }
    try:
        cursor = account.get_insights(fields=fields, params=params)
        raw = _seg_paginate(cursor)
    except Exception as exc:
        print(f"    [warn] insights fetch failed for {campaign_label}: {exc}")
        return []

    rows = []
    for r in raw:
        rows.append({
            "campaign_id":   campaign_id,
            "campaign_name": campaign_label,
            "adset_id":      str(r.get("adset_id", "")),
            "adset_name":    str(r.get("adset_name", "")),
            "spend":         float(r.get("spend", 0) or 0),
            "impressions":   int(r.get("impressions", 0) or 0),
            "link_clicks":   int(r.get("inline_link_clicks", 0) or 0),
            "reach":         int(r.get("reach", 0) or 0),
            "fsd":           _seg_extract_fsd(r.get("conversions")),
        })
    return rows


def _seg_aggregate(all_rows: list[dict]) -> dict[str, dict]:
    """Aggregate adset rows by segment. Returns {segment: agg_dict}."""
    agg: dict[str, dict] = {}
    for seg in SEGMENT_MAP:
        agg[seg] = {
            "total_spend": 0.0, "total_impr": 0, "total_link_clicks": 0,
            "total_fsd": 0.0, "total_reach": 0,
        }
    for row in all_rows:
        seg = _seg_classify(row["adset_name"])
        if seg is None:
            continue
        a = agg[seg]
        a["total_spend"]       += row["spend"]
        a["total_impr"]        += row["impressions"]
        a["total_link_clicks"] += row["link_clicks"]
        a["total_fsd"]         += row["fsd"]
        a["total_reach"]       += row["reach"]

    for seg, a in agg.items():
        clicks = a["total_link_clicks"] or 1
        impr   = a["total_impr"] or 1
        spend  = a["total_spend"]
        a["avg_ctr"] = (a["total_link_clicks"] / impr * 100) if a["total_impr"] else 0.0
        a["avg_cpc"] = (spend / clicks) if a["total_link_clicks"] else 0.0
        a["avg_cpm"] = (spend / impr * 1000) if a["total_impr"] else 0.0

    return agg


# ---------------------------------------------------------------------------
# SEGMENT DATA LOADERS
# ---------------------------------------------------------------------------

def load_segment_data(settings) -> dict[str, dict]:
    """
    Pull adset-level insights from Meta API for all campaigns, aggregate by segment.
    Returns {segment_name: {total_spend, total_impr, total_link_clicks, total_fsd,
                             total_reach, avg_ctr, avg_cpc, avg_cpm}}
    """
    _seg_init_api(settings)
    print("  Fetching segment data from Meta API ...")
    all_rows: list[dict] = []
    time_range = {"since": SEGMENT_SINCE, "until": SEGMENT_UNTIL}
    for cid, clabel in ALL_CAMPAIGNS_FOR_SEGMENTS.items():
        print(f"    {clabel}")
        rows = _seg_fetch_adset_insights(settings, cid, clabel, time_range)
        all_rows.extend(rows)
    print(f"  Total adset rows: {len(all_rows)}")
    agg = _seg_aggregate(all_rows)

    # Print segment totals to console for verification
    print()
    print(f"{'Segment':<28} {'Spend':>10} {'Impr':>8} {'Clicks':>8} {'FSD':>6} {'CTR%':>7} {'CPC':>7} {'CPM':>7}")
    print("-" * 88)
    for seg in sorted(agg.keys(), key=lambda s: -agg[s]["total_fsd"]):
        a = agg[seg]
        print(
            f"{seg:<28} ${a['total_spend']:>9,.2f} {a['total_impr']:>8,} "
            f"{a['total_link_clicks']:>8,} {int(a['total_fsd']):>6} "
            f"{a['avg_ctr']:>6.2f}% ${a['avg_cpc']:>5.3f} ${a['avg_cpm']:>6.2f}"
        )
    print()
    return agg


def load_stripe_by_segment() -> dict[str, dict]:
    """
    Load Stripe paid/pending counts from DB, keyed by segment name.
    Returns {segment_name: {paid, pending, total}}
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    result: dict[str, dict] = {
        seg: {"paid": 0, "pending": 0, "total": 0} for seg in SEGMENT_MAP
    }
    for source, segment in STRIPE_SOURCE_MAP_SEG.items():
        cur.execute(
            "SELECT COUNT(*) FROM stripe_payments WHERE source=? AND status='paid'", (source,)
        )
        paid = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM stripe_payments WHERE source=? AND status!='paid'", (source,)
        )
        pending = cur.fetchone()[0]
        result[segment]["paid"]    = paid
        result[segment]["pending"] = pending
        result[segment]["total"]   = paid + pending
    conn.close()
    return result


def load_weekly_segment_data(settings) -> dict[str, list[tuple]]:
    """
    Pull weekly adset-level data for the top-3 segments.
    Returns {segment_name: [(week_label, spend, fsd, ctr, cpc), ...]}
    Weeks: W1 May 13-19, W2 May 20-26, W3 May 27-31.
    """
    _seg_init_api(settings)
    weeks = [
        ("Week 1 (May 13-19)", {"since": "2026-05-13", "until": "2026-05-19"}),
        ("Week 2 (May 20-26)", {"since": "2026-05-20", "until": "2026-05-26"}),
        ("Week 3 (May 27-31)", {"since": "2026-05-27", "until": "2026-05-31"}),
    ]
    top3_segments = {"Nostalgia Bridge Dad", "Sturdy Parenting", "Routine-Chaos"}

    # Accumulate per segment per week
    seg_week: dict[str, dict[str, dict]] = {
        seg: {} for seg in top3_segments
    }

    for week_label, time_range in weeks:
        print(f"  Fetching weekly data: {week_label}")
        all_rows: list[dict] = []
        for cid, clabel in ALL_CAMPAIGNS_FOR_SEGMENTS.items():
            rows = _seg_fetch_adset_insights(settings, cid, clabel, time_range)
            all_rows.extend(rows)

        # Aggregate per segment for this week
        for row in all_rows:
            seg = _seg_classify(row["adset_name"])
            if seg not in top3_segments:
                continue
            if week_label not in seg_week[seg]:
                seg_week[seg][week_label] = {
                    "spend": 0.0, "fsd": 0.0,
                    "ctr_wsum": 0.0, "cpc_wsum": 0.0, "w_spend": 0.0,
                    "impressions": 0, "link_clicks": 0,
                }
            w = seg_week[seg][week_label]
            w["spend"]      += row["spend"]
            w["fsd"]        += row["fsd"]
            w["impressions"] += row["impressions"]
            w["link_clicks"] += row["link_clicks"]

    # Convert to list of tuples for each segment
    result: dict[str, list[tuple]] = {}
    for seg in top3_segments:
        tuples = []
        for week_label, _ in weeks:
            w = seg_week[seg].get(week_label, {})
            spend  = w.get("spend", 0.0)
            fsd    = w.get("fsd", 0.0)
            impr   = w.get("impressions", 0)
            clicks = w.get("link_clicks", 0)
            avg_ctr = (clicks / impr * 100) if impr else 0.0
            avg_cpc = (spend / clicks) if clicks else 0.0
            tuples.append((week_label, spend, fsd, avg_ctr, avg_cpc))
        result[seg] = tuples

    return result


def load_segment_daily_cpr(settings) -> dict[str, list[dict]]:
    """
    Pull daily adset-level data for top-3 segments to compute daily CPR.
    Returns {segment_name: [{date, spend, fsd, cpr}, ...]}
    """
    _seg_init_api(settings)
    top3_segments = {"Nostalgia Bridge Dad", "Sturdy Parenting", "Routine-Chaos"}

    # Fetch daily time breakdown for each campaign
    from facebook_business.adobjects.adaccount import AdAccount
    account = AdAccount(f"act_{settings.meta_ad_account_id.removeprefix('act_')}")

    # Accumulate per segment per date
    seg_date: dict[str, dict[str, dict]] = {seg: {} for seg in top3_segments}

    for cid, clabel in ALL_CAMPAIGNS_FOR_SEGMENTS.items():
        fields = ["adset_id", "adset_name", "spend", "conversions", "date_start"]
        params = {
            "level": "adset",
            "filtering": [{"field": "campaign.id", "operator": "EQUAL", "value": cid}],
            "time_range": {"since": SEGMENT_SINCE, "until": SEGMENT_UNTIL},
            "time_increment": 1,
            "limit": 2000,
        }
        try:
            cursor = account.get_insights(fields=fields, params=params)
            raw = _seg_paginate(cursor)
        except Exception as exc:
            print(f"    [warn] daily fetch failed for {clabel}: {exc}")
            continue

        for r in raw:
            seg = _seg_classify(str(r.get("adset_name", "")))
            if seg not in top3_segments:
                continue
            date = str(r.get("date_start", ""))
            if not date:
                continue
            spend = float(r.get("spend", 0) or 0)
            fsd   = _seg_extract_fsd(r.get("conversions"))

            if date not in seg_date[seg]:
                seg_date[seg][date] = {"spend": 0.0, "fsd": 0.0}
            seg_date[seg][date]["spend"] += spend
            seg_date[seg][date]["fsd"]   += fsd

    result: dict[str, list[dict]] = {}
    for seg in top3_segments:
        rows = []
        for date, vals in sorted(seg_date[seg].items()):
            cpr = (vals["spend"] / vals["fsd"]) if vals["fsd"] > 0 else None
            rows.append({"date": date, "spend": vals["spend"], "fsd": vals["fsd"], "cpr": cpr})
        result[seg] = rows

    return result


def load_segment_ad_performance(settings) -> list[dict]:
    """
    Pull ad-level metrics across ALL campaigns to feed Section 7 (format/angle verdict).
    Returns list of {ad_format, ad_style, avg_ctr, avg_cpc, total_fsd}.
    """
    _seg_init_api(settings)
    from facebook_business.adobjects.adaccount import AdAccount
    account = AdAccount(f"act_{settings.meta_ad_account_id.removeprefix('act_')}")

    fields = ["ad_id", "ad_name", "spend", "inline_link_click_ctr", "cpc", "conversions"]
    params = {
        "level": "ad",
        "time_range": {"since": SEGMENT_SINCE, "until": SEGMENT_UNTIL},
        "limit": 1000,
    }
    try:
        cursor = account.get_insights(fields=fields, params=params)
        raw = _seg_paginate(cursor)
    except Exception as exc:
        print(f"  [warn] ad-level insights fetch failed: {exc}")
        return []

    rows = []
    for r in raw:
        ad_name = str(r.get("ad_name", ""))
        # Derive format and style from ad name conventions
        name_upper = ad_name.upper()
        if "VIDEO" in name_upper or "VID" in name_upper:
            ad_format = "video"
        elif "CAROUSEL" in name_upper:
            ad_format = "carousel"
        else:
            ad_format = "image"

        # Style / angle from name keywords
        if "TESTIMONIAL" in name_upper:
            ad_style = "testimonial"
        elif "HERO" in name_upper or "PRODUCT" in name_upper:
            ad_style = "product_hero"
        elif "NATIVE" in name_upper or "UI" in name_upper:
            ad_style = "native_ui"
        elif "TRANSFORM" in name_upper or "BEFORE" in name_upper:
            ad_style = "transformation_proof"
        elif "CONTRAST" in name_upper:
            ad_style = "contrast_repositioning"
        elif "NOSTALGIA" in name_upper or "NOSTBRD" in name_upper:
            ad_style = "nostalgia_emotional"
        elif "ROUTINE" in name_upper:
            ad_style = "contrast_repositioning"
        elif "STURDY" in name_upper or "EVIDENCE" in name_upper:
            ad_style = "product_hero"
        else:
            ad_style = "other"

        rows.append({
            "ad_id":     str(r.get("ad_id", "")),
            "ad_name":   ad_name,
            "ad_format": ad_format,
            "ad_style":  ad_style,
            "avg_ctr":   float(r.get("inline_link_click_ctr", 0) or 0),
            "avg_cpc":   float(r.get("cpc", 0) or 0),
            "total_fsd": _seg_extract_fsd(r.get("conversions")),
        })
    return rows


# ---------------------------------------------------------------------------
# READ STRIPE FROM DB (stripe_payments table — most up to date)
# ---------------------------------------------------------------------------
def load_stripe_data():
    """Returns dict: campaign_name → {'paid': int, 'pending': int, 'total': int}
    Reads from DB stripe_payments table (more up to date than the CSV snapshot).
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    result = {}
    for source, campaign in SOURCE_TO_CAMPAIGN.items():
        cur.execute("SELECT COUNT(*) FROM stripe_payments WHERE source=? AND status='paid'", (source,))
        paid = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM stripe_payments WHERE source=? AND status!='paid'", (source,))
        pending = cur.fetchone()[0]
        result[campaign] = {"paid": paid, "pending": pending, "total": paid + pending}
    conn.close()
    return result


# ---------------------------------------------------------------------------
# WEEKLY DATA FROM DB
# ---------------------------------------------------------------------------
def load_weekly_data():
    """Returns dict: campaign_name → list of (week_label, spend, fsd, ctr, cpc)"""
    campaign_id_map = {
        "Nostalgia Bridge Dad": "120243727739260025",
        "Sturdy Parenting":     "120243727242320025",
        "Routine-Chaos":        "120243727123190025",
    }
    id_to_name = {v: k for k, v in campaign_id_map.items()}

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT campaign_id,
               CASE WHEN date < '2026-05-20' THEN 'Week 1 (May 13-19)'
                    WHEN date < '2026-05-27' THEN 'Week 2 (May 20-26)'
                    ELSE 'Week 3 (May 27-28)' END as week,
               ROUND(SUM(spend),2),
               SUM(meta_form_submit_deposit),
               ROUND(AVG(ctr),2),
               ROUND(AVG(cpc),3)
        FROM ad_metrics
        WHERE campaign_id IN ('120243727739260025','120243727242320025','120243727123190025')
          AND ad_set_id = ''
        GROUP BY campaign_id, week
        ORDER BY campaign_id, week
    """)
    rows = cur.fetchall()
    conn.close()

    result = defaultdict(list)
    for campaign_id, week, spend, fsd, ctr, cpc in rows:
        name = id_to_name.get(campaign_id, campaign_id)
        result[name].append((week, spend, fsd, ctr, cpc))

    return result


# ---------------------------------------------------------------------------
# LOAD EXTRA DATA (device breakdown, ad performance, daily CPR, weekly CAC)
# ---------------------------------------------------------------------------
def load_extra_data():
    """Load the JSON produced by fetch_extra_data.py. Returns empty dict if missing."""
    if not EXTRA_DATA_PATH.exists():
        print(f"  [warn] {EXTRA_DATA_PATH} not found — extra sections will use fallback data")
        return {}
    with open(EXTRA_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def cpac(spend, paid):
    return spend / paid if paid > 0 else None

def fmt_cpac(val):
    return f"${val:.2f}" if val is not None else "—"

def pct(num, denom):
    return (num / denom * 100) if denom > 0 else 0

def bar(value, max_val, color, height=12, label=""):
    w = max(2, int(value / max_val * 200)) if max_val > 0 else 2
    return f'<div style="background:{color};height:{height}px;width:{w}px;border-radius:2px;display:inline-block;" title="{label}"></div>'

def arrow_icon(direction):
    """Return an HTML arrow with color for trend direction."""
    if direction == "up":
        return '<span style="color:#34d399;font-size:16px;">&#8593;</span>'
    elif direction == "down":
        return '<span style="color:#f59e0b;font-size:16px;">&#8595;</span>'
    else:
        return '<span style="color:#64748b;font-size:16px;">&#8596;</span>'


# ============================================================================
# ============================================================================
# INVESTOR REPORT
# ============================================================================
# ============================================================================
def build_investor_report(stripe, extra):
    total_spend = sum(c[1] for c in CAMPAIGNS)
    total_fsd = sum(c[7] for c in CAMPAIGNS)
    total_paid = sum(stripe[c[0]]["paid"] for c in CAMPAIGNS)
    top3_spend = sum(c[1] for c in CAMPAIGNS[:3])
    overall_paid_rate = pct(total_paid, total_fsd)

    # ----------------------------------------------------------------
    # Badge logic
    # ----------------------------------------------------------------
    def badge(name):
        top3 = {"Nostalgia Bridge Dad", "Sturdy Parenting", "Routine-Chaos"}
        strong = {"Anxiety Regulation", "ADHD-EF Intervention"}
        if name in top3:
            return '<span class="badge winner">Winner &#127942;</span>'
        elif name in strong:
            return '<span class="badge strong">Strong</span>'
        else:
            return '<span class="badge cut">Cut</span>'

    ranked = sorted(CAMPAIGNS, key=lambda c: c[7], reverse=True)
    max_fsd = ranked[0][7]
    max_ctr = max(c[4] for c in CAMPAIGNS)

    # ----------------------------------------------------------------
    # Campaign cards
    # ----------------------------------------------------------------
    cards_html = ""
    for name, spend, impr, clicks, ctr, cpc, cpm, fsd, days, dr in ranked:
        paid = stripe[name]["paid"]
        cp = cpac(spend, paid)
        color = CAMPAIGN_COLORS.get(name, "#94a3b8")
        fsd_bar = bar(fsd, max_fsd, color, 10, f"{fsd} FSD")
        ctr_bar = bar(ctr, max_ctr, color, 10, f"{ctr}% CTR")
        cards_html += f"""
        <div class="rank-card">
          <div class="rank-card-header">
            <span class="campaign-name" style="color:{color}">{name}</span>
            {badge(name)}
          </div>
          <div class="rank-metrics">
            <div class="metric-item">
              <div class="metric-label">CTR</div>
              <div class="metric-value">{ctr}%</div>
              {ctr_bar}
            </div>
            <div class="metric-item">
              <div class="metric-label">FSD</div>
              <div class="metric-value">{fsd}</div>
              {fsd_bar}
            </div>
            <div class="metric-item">
              <div class="metric-label">Paid Deposits</div>
              <div class="metric-value">{paid}</div>
            </div>
            <div class="metric-item">
              <div class="metric-label">CPaC</div>
              <div class="metric-value">{fmt_cpac(cp)}</div>
            </div>
          </div>
        </div>"""

    # ----------------------------------------------------------------
    # Top 3 funnels
    # ----------------------------------------------------------------
    top3_data = [
        ("Nostalgia Bridge Dad", "#f59e0b", 28370, 2990, 11.19, 52, stripe["Nostalgia Bridge Dad"]["paid"]),
        ("Sturdy Parenting",     "#34d399", 18289, 1670, 10.22, 48, stripe["Sturdy Parenting"]["paid"]),
        ("Routine-Chaos",        "#f472b6", 22547, 1442,  6.81, 30, stripe["Routine-Chaos"]["paid"]),
    ]

    funnel_html = ""
    for name, color, impr, clicks, ctr, fsd, paid in top3_data:
        cp = cpac(dict((c[0], c[1]) for c in CAMPAIGNS)[name], paid)
        paid_rate = pct(paid, fsd)

        funnel_html += f"""
        <div class="funnel-card">
          <div class="funnel-title" style="color:{color}">{name}</div>
          <div class="funnel-steps">
            <div class="funnel-step">
              <div class="funnel-bar" style="background:{color}22;border:1px solid {color}44;width:100%">
                <span class="funnel-num">{impr:,}</span>
                <span class="funnel-lbl">Impressions</span>
              </div>
            </div>
            <div class="funnel-arrow">&#9660;</div>
            <div class="funnel-step">
              <div class="funnel-bar" style="background:{color}33;border:1px solid {color}66;width:85%">
                <span class="funnel-num">{clicks:,}</span>
                <span class="funnel-lbl">Clicks <span class="funnel-pct">CTR {ctr}%</span></span>
              </div>
            </div>
            <div class="funnel-arrow">&#9660;</div>
            <div class="funnel-step">
              <div class="funnel-bar" style="background:{color}55;border:1px solid {color}99;width:60%">
                <span class="funnel-num">{fsd}</span>
                <span class="funnel-lbl">Form Submissions <span class="funnel-pct">{pct(fsd,clicks):.1f}% of clicks</span></span>
              </div>
            </div>
            <div class="funnel-arrow">&#9660;</div>
            <div class="funnel-step">
              <div class="funnel-bar" style="background:{color};border:1px solid {color};width:40%">
                <span class="funnel-num" style="color:#0d1018">{paid}</span>
                <span class="funnel-lbl" style="color:#0d1018">Paid Deposits <span class="funnel-pct" style="color:#0d101888">{paid_rate:.1f}% paid</span></span>
              </div>
            </div>
            <div class="funnel-cpac">CPaC: <strong>{fmt_cpac(cp)}</strong></div>
          </div>
        </div>"""

    # ----------------------------------------------------------------
    # Section 4: Why These 3 Won (enhanced with first-touchpoint lines)
    # ----------------------------------------------------------------
    nb_paid = stripe["Nostalgia Bridge Dad"]["paid"]
    sp_paid = stripe["Sturdy Parenting"]["paid"]
    rc_paid = stripe["Routine-Chaos"]["paid"]
    nb_cpac = fmt_cpac(cpac(831.66, nb_paid))
    sp_cpac = fmt_cpac(cpac(799.82, sp_paid))
    rc_cpac = fmt_cpac(cpac(793.92, rc_paid))
    sp_paid_rate = pct(sp_paid, 48)  # 48 FSD for Sturdy

    insight_html = f"""
    <div class="insight-card" style="border-top:3px solid #f59e0b">
      <div class="insight-name" style="color:#f59e0b">Nostalgia Bridge Dad</div>
      <div class="insight-stat">11.19% CTR &nbsp;·&nbsp; $0.246 CPC &nbsp;·&nbsp; CPaC {nb_cpac}</div>
      <div class="insight-touchpoint">
        <span class="touchpoint-label">First touchpoint:</span>
        Reached fathers via a nostalgia and emotional resonance angle — the lowest friction entry point
        in the cohort (11.2% CTR, $0.25 CPC) → highest paid deposits ({nb_paid}) of all campaigns.
      </div>
      <div class="insight-text">The highest click-through rate in the cohort paired with the lowest cost-per-click signals an audience that deeply identifies with the messaging. Parents in this segment self-select aggressively — they are already primed to act, making every ad dollar work hardest here.</div>
    </div>
    <div class="insight-card" style="border-top:3px solid #34d399">
      <div class="insight-name" style="color:#34d399">Sturdy Parenting</div>
      <div class="insight-stat">48 form submissions &nbsp;·&nbsp; CPaC {sp_cpac}</div>
      <div class="insight-touchpoint">
        <span class="touchpoint-label">First touchpoint:</span>
        Reached structured, evidence-based parents seeking a science-backed framework → strong
        FSD-to-paid conversion ({sp_paid}/{48} = {sp_paid_rate:.0f}%), signaling genuine purchase intent
        and a high-quality audience match.
      </div>
      <div class="insight-text">The second-highest FSD volume across all 10 campaigns, sustained over 16 full days. Parents looking for evidence-based parenting frameworks converted at a strong rate, suggesting a large, addressable audience with genuine purchase intent.</div>
    </div>
    <div class="insight-card" style="border-top:3px solid #f472b6">
      <div class="insight-name" style="color:#f472b6">Routine-Chaos</div>
      <div class="insight-stat">CPaC {rc_cpac} &nbsp;·&nbsp; 16-day consistency</div>
      <div class="insight-touchpoint">
        <span class="touchpoint-label">First touchpoint:</span>
        Reached parents overwhelmed by daily household chaos → consistent volume across the full test window
        with a competitive $30.73 CPaC, suggesting a scalable segment with dependable conversion behavior.
      </div>
      <div class="insight-text">While CTR was more modest, Routine-Chaos maintained steady FSD delivery across the full test window — no dropoff, no fatigue. This consistency and competitive CPaC suggest a scalable segment with dependable conversion behavior.</div>
    </div>"""

    # ----------------------------------------------------------------
    # Section 4b: Phase 2 Validation (PC Only | Top Static | Top Video)
    # ----------------------------------------------------------------
    validation_cards = [
        {
            "label": "PC Only",
            "date_range": "May 26–31",
            "spend": 144.09,
            "fsd": 4,
            "rate": round(4 / 144.09 * 100, 1),
            "finding": "PC delivers cheap clicks but poor form submission rate",
            "color": "#818cf8",
        },
        {
            "label": "Top Static",
            "date_range": "May 28–31",
            "spend": 525.64,
            "fsd": 35,
            "rate": round(35 / 525.64 * 100, 1),
            "finding": "Static wins — avg CTR 5.73%, avg CPC $0.53, cheapest acquisition",
            "color": "#34d399",
        },
        {
            "label": "Top Video",
            "date_range": "May 28–31",
            "spend": 324.35,
            "fsd": 20,
            "rate": round(20 / 324.35 * 100, 1),
            "finding": "Video drives FSD but at 3× the cost-per-click of static",
            "color": "#f59e0b",
        },
    ]

    val_cards_html = ""
    for vc in validation_cards:
        val_cards_html += f"""
        <div class="val-card" style="border-top:3px solid {vc['color']}">
          <div class="val-card-label" style="color:{vc['color']}">{vc['label']}</div>
          <div class="val-card-range">{vc['date_range']}</div>
          <div class="val-card-metrics">
            <div class="val-metric">
              <div class="val-metric-label">Total Spend</div>
              <div class="val-metric-value">${vc['spend']:,.2f}</div>
            </div>
            <div class="val-metric">
              <div class="val-metric-label">FSD</div>
              <div class="val-metric-value">{vc['fsd']}</div>
            </div>
            <div class="val-metric">
              <div class="val-metric-label">FSD / $100</div>
              <div class="val-metric-value" style="color:{vc['color']}">{vc['rate']}</div>
            </div>
          </div>
          <div class="val-finding">{vc['finding']}</div>
        </div>"""

    # Static vs Video bar comparison
    static_metrics = [("CTR", "7.89%", 7.89), ("CPC", "$0.30", 0.30), ("CPM", "$27", 27)]
    video_metrics  = [("CTR", "5.22%", 5.22), ("CPC", "$0.93", 0.93), ("CPM", "$48", 48)]
    bar_max_vals   = [max(7.89, 5.22), max(0.30, 0.93), max(27, 48)]

    sv_rows = ""
    for i, (lbl, _, _) in enumerate(static_metrics):
        sv, vv = static_metrics[i][1], video_metrics[i][1]
        sv_raw, vv_raw = static_metrics[i][2], video_metrics[i][2]
        mx = bar_max_vals[i]
        sw = max(4, int(sv_raw / mx * 160))
        vw = max(4, int(vv_raw / mx * 160))
        sv_rows += f"""
        <div class="sv-row">
          <div class="sv-label">{lbl}</div>
          <div class="sv-bars">
            <div class="sv-bar-wrap">
              <div class="sv-bar" style="background:#34d399;width:{sw}px"></div>
              <span class="sv-val">{sv}</span>
            </div>
            <div class="sv-bar-wrap">
              <div class="sv-bar" style="background:#f59e0b;width:{vw}px"></div>
              <span class="sv-val">{vv}</span>
            </div>
          </div>
        </div>"""

    # Nostalgia CTR across formats
    nb_format_rows = [
        ("Static",  "9.04%", "#34d399"),
        ("PC Only", "7.06%", "#818cf8"),
        ("Video",   "7.66%", "#f59e0b"),
    ]
    nb_rows_html = ""
    for fmt, ctr_val, color in nb_format_rows:
        w = max(4, int(float(ctr_val.rstrip('%')) / 10 * 160))
        nb_rows_html += f"""
        <div class="sv-row">
          <div class="sv-label">{fmt}</div>
          <div class="sv-bars">
            <div class="sv-bar-wrap">
              <div class="sv-bar" style="background:{color};width:{w}px"></div>
              <span class="sv-val" style="color:{color}">{ctr_val}</span>
            </div>
          </div>
        </div>"""

    phase2_section_html = f"""
    <!-- SECTION 4b: PHASE 2 VALIDATION -->
    <section>
      <div class="container">
        <div class="section-label">Phase 2</div>
        <h2 class="section-title">Phase 2: Validation</h2>
        <p class="section-desc">After identifying the top 3 segments, we ran a validation phase to confirm format and placement efficiency. Three parallel campaigns tested PC-only placement, static ads, and video ads across the same winning segments.</p>

        <div class="val-cards">
          {val_cards_html}
        </div>

        <div class="val-insights">
          <div class="val-insight-panel">
            <div class="val-insight-title">Static vs Video</div>
            <div class="val-legend">
              <span class="val-legend-dot" style="background:#34d399"></span> Static &nbsp;
              <span class="val-legend-dot" style="background:#f59e0b"></span> Video
            </div>
            {sv_rows}
          </div>
          <div class="val-insight-panel">
            <div class="val-insight-title">Nostalgia Bridge — consistent across all formats</div>
            <div class="val-legend" style="color:#64748b;font-size:12px">CTR by format</div>
            {nb_rows_html}
            <div class="val-nb-note">Nostalgia Bridge wins on CTR in every single format tested — the most reliable segment in the cohort.</div>
          </div>
        </div>
      </div>
    </section>"""

    # ----------------------------------------------------------------
    # Section 5: CAC Trend (Week 1 → Week 2 → Week 3)
    # ----------------------------------------------------------------
    # weekly_cac from extra_data; compute CAC per week
    # Total paid by campaign from Stripe; distribute proportionally by weekly FSD
    weekly_cac_raw = extra.get("weekly_cac", [])

    # Build dict: campaign_id → {week: {spend, fsd}}
    weekly_by_camp = defaultdict(dict)
    for row in weekly_cac_raw:
        weekly_by_camp[row["campaign_id"]][row["week"]] = {
            "spend": row["spend"],
            "fsd": row["fsd"],
        }

    def build_cac_trend_card(name, color, paid_total):
        cid = NAME_TO_ID[name]
        weeks_data = weekly_by_camp.get(cid, {})

        # Total FSD across weeks (for proportional distribution)
        total_fsd_weeks = sum(w["fsd"] for w in weeks_data.values())

        cards = []
        for week_label in ["Week 1", "Week 2", "Week 3"]:
            wdata = weeks_data.get(week_label, {"spend": 0, "fsd": 0})
            w_spend = wdata["spend"]
            w_fsd = wdata["fsd"]
            # Approximate paid deposits proportionally to FSD weight
            w_paid = (w_fsd / total_fsd_weeks * paid_total) if total_fsd_weeks > 0 else 0
            w_cac = w_spend / w_paid if w_paid > 0 else None
            cards.append((week_label, w_spend, w_fsd, w_paid, w_cac))

        # Compute trend direction W1 → W3
        cac_w1 = cards[0][4]
        cac_w3 = cards[2][4]
        if cac_w1 and cac_w3:
            pct_change = (cac_w3 - cac_w1) / cac_w1 * 100
            if pct_change < -5:
                trend_dir = "down"
                trend_label = f"{abs(pct_change):.0f}% cheaper"
                trend_color = "#34d399"
            elif pct_change > 5:
                trend_dir = "up"
                trend_label = f"{abs(pct_change):.0f}% more expensive"
                trend_color = "#f87171"
            else:
                trend_dir = "flat"
                trend_label = "Stable"
                trend_color = "#94a3b8"
        else:
            trend_dir = "flat"
            trend_label = "—"
            trend_color = "#94a3b8"

        # Build week blocks
        week_blocks = ""
        for i, (wlabel, w_spend, w_fsd, w_paid, w_cac) in enumerate(cards):
            short = wlabel.replace("Week ", "W")
            cac_str = fmt_cpac(w_cac)
            week_blocks += f"""
            <div class="cac-week">
              <div class="cac-week-label" style="color:{color}">{short}</div>
              <div class="cac-week-value">{cac_str}</div>
              <div class="cac-week-sub">{w_fsd} FSD · ${w_spend:,.0f}</div>
              {"" if i == len(cards)-1 else '<div class="cac-week-arrow">&#8594;</div>'}
            </div>"""

        return f"""
        <div class="cac-card" style="border-top:3px solid {color}">
          <div class="cac-title" style="color:{color}">{name}</div>
          <div class="cac-trend-badge" style="color:{trend_color};background:{trend_color}18;border:1px solid {trend_color}44">
            {arrow_icon(trend_dir)} {trend_label}
          </div>
          <div class="cac-weeks">{week_blocks}</div>
          <div class="cac-note">CPaC = weekly spend ÷ proportional paid deposits (by FSD weight). W3 = May 27–31.</div>
        </div>"""

    cac_trend_html = (
        build_cac_trend_card("Nostalgia Bridge Dad", "#f59e0b", nb_paid)
        + build_cac_trend_card("Sturdy Parenting", "#34d399", sp_paid)
        + build_cac_trend_card("Routine-Chaos", "#f472b6", rc_paid)
    )

    # ----------------------------------------------------------------
    # Section 6: Investment Summary (unchanged)
    # ----------------------------------------------------------------
    cut_spend = total_spend - top3_spend

    # ================================================================
    # ASSEMBLE INVESTOR HTML
    # ================================================================
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Nowa — Investor Report — May 2026</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #080b12; color: #e4e7ef; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; line-height: 1.6; }}
  .container {{ max-width: 1100px; margin: 0 auto; padding: 0 24px; }}

  /* HERO */
  .hero {{ background: linear-gradient(135deg, #0d1018 0%, #0f172a 50%, #080b12 100%); padding: 80px 0 60px; border-bottom: 1px solid #1e2535; }}
  .hero-eyebrow {{ font-size: 12px; letter-spacing: 3px; color: #f59e0b; text-transform: uppercase; margin-bottom: 20px; }}
  .hero-title {{ font-size: 42px; font-weight: 700; line-height: 1.15; color: #f8fafc; margin-bottom: 14px; }}
  .hero-subtitle {{ font-size: 18px; color: #94a3b8; margin-bottom: 48px; }}
  .hero-stats {{ display: flex; gap: 0; border: 1px solid #1e2535; border-radius: 12px; overflow: hidden; }}
  .hero-stat {{ flex: 1; padding: 24px 20px; background: #0d1018; border-right: 1px solid #1e2535; text-align: center; }}
  .hero-stat:last-child {{ border-right: none; }}
  .hero-stat-value {{ font-size: 32px; font-weight: 700; color: #f59e0b; }}
  .hero-stat-label {{ font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: 1px; margin-top: 4px; }}
  .date-range {{ margin-top: 32px; font-size: 13px; color: #475569; }}

  /* SECTIONS */
  section {{ padding: 64px 0; border-bottom: 1px solid #0f172a; }}
  .section-label {{ font-size: 11px; letter-spacing: 3px; text-transform: uppercase; color: #f59e0b; margin-bottom: 12px; }}
  .section-title {{ font-size: 28px; font-weight: 700; color: #f8fafc; margin-bottom: 8px; }}
  .section-desc {{ font-size: 15px; color: #64748b; max-width: 640px; margin-bottom: 40px; }}

  /* ELIMINATION FUNNEL */
  .elim-funnel {{ display: flex; align-items: center; gap: 0; flex-wrap: wrap; margin-top: 32px; }}
  .elim-stage {{ text-align: center; padding: 20px 24px; background: #0d1018; border: 1px solid #1e2535; border-radius: 8px; min-width: 130px; }}
  .elim-num {{ font-size: 40px; font-weight: 800; color: #f59e0b; }}
  .elim-label {{ font-size: 12px; color: #94a3b8; margin-top: 4px; }}
  .elim-date {{ font-size: 11px; color: #475569; margin-top: 6px; }}
  .elim-arrow {{ font-size: 24px; color: #334155; padding: 0 8px; }}
  .elim-cut {{ font-size: 11px; color: #ef4444; background: #1a0a0a; border: 1px solid #3d1010; border-radius: 4px; padding: 3px 8px; margin-top: 6px; display: inline-block; }}

  /* RANK CARDS */
  .rank-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }}
  .rank-card {{ background: #0d1018; border: 1px solid #1e2535; border-radius: 10px; padding: 20px; }}
  .rank-card-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }}
  .campaign-name {{ font-size: 15px; font-weight: 600; }}
  .badge {{ font-size: 11px; padding: 3px 10px; border-radius: 20px; font-weight: 600; white-space: nowrap; }}
  .badge.winner {{ background: #422006; color: #f59e0b; border: 1px solid #78350f; }}
  .badge.strong {{ background: #0c2a1f; color: #34d399; border: 1px solid #065f46; }}
  .badge.cut {{ background: #1a1f2e; color: #475569; border: 1px solid #334155; }}
  .rank-metrics {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
  .metric-label {{ font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: #475569; margin-bottom: 2px; }}
  .metric-value {{ font-size: 18px; font-weight: 700; color: #e4e7ef; margin-bottom: 4px; }}

  /* FUNNELS */
  .funnel-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }}
  @media (max-width: 800px) {{ .funnel-grid {{ grid-template-columns: 1fr; }} }}
  .funnel-card {{ background: #0d1018; border: 1px solid #1e2535; border-radius: 10px; padding: 24px; }}
  .funnel-title {{ font-size: 16px; font-weight: 700; margin-bottom: 20px; }}
  .funnel-steps {{ display: flex; flex-direction: column; gap: 4px; }}
  .funnel-bar {{ padding: 10px 14px; border-radius: 6px; display: flex; justify-content: space-between; align-items: center; }}
  .funnel-num {{ font-size: 18px; font-weight: 700; }}
  .funnel-lbl {{ font-size: 12px; color: #94a3b8; text-align: right; }}
  .funnel-pct {{ display: block; font-size: 11px; color: #64748b; }}
  .funnel-arrow {{ text-align: center; color: #334155; font-size: 14px; margin: 2px 0; }}
  .funnel-cpac {{ margin-top: 12px; font-size: 13px; color: #64748b; text-align: center; }}
  .funnel-cpac strong {{ color: #e4e7ef; font-size: 16px; }}

  /* INSIGHTS (Why These 3 Won) */
  .insight-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }}
  @media (max-width: 800px) {{ .insight-grid {{ grid-template-columns: 1fr; }} }}
  .insight-card {{ background: #0d1018; border: 1px solid #1e2535; border-radius: 10px; padding: 24px; }}
  .insight-name {{ font-size: 16px; font-weight: 700; margin-bottom: 8px; }}
  .insight-stat {{ font-size: 13px; color: #94a3b8; margin-bottom: 14px; background: #080b12; padding: 8px 12px; border-radius: 6px; }}
  .insight-touchpoint {{ font-size: 13px; color: #cbd5e1; background: #0f172a; border-left: 3px solid #334155; padding: 10px 14px; border-radius: 0 6px 6px 0; margin-bottom: 14px; line-height: 1.6; }}
  .touchpoint-label {{ font-weight: 700; color: #64748b; text-transform: uppercase; font-size: 10px; letter-spacing: 1px; display: block; margin-bottom: 4px; }}
  .insight-text {{ font-size: 14px; color: #94a3b8; line-height: 1.7; }}

  /* CAC TREND */
  .cac-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }}
  @media (max-width: 800px) {{ .cac-grid {{ grid-template-columns: 1fr; }} }}
  .cac-card {{ background: #0d1018; border: 1px solid #1e2535; border-radius: 10px; padding: 24px; }}
  .cac-title {{ font-size: 15px; font-weight: 700; margin-bottom: 10px; }}
  .cac-trend-badge {{ display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600; padding: 4px 12px; border-radius: 20px; margin-bottom: 20px; }}
  .cac-weeks {{ display: flex; align-items: center; gap: 0; flex-wrap: wrap; }}
  .cac-week {{ display: flex; flex-direction: column; align-items: center; padding: 12px 10px; background: #080b12; border: 1px solid #1e2535; border-radius: 8px; min-width: 72px; }}
  .cac-week-label {{ font-size: 10px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; font-weight: 700; }}
  .cac-week-value {{ font-size: 20px; font-weight: 800; color: #e4e7ef; }}
  .cac-week-sub {{ font-size: 10px; color: #475569; margin-top: 4px; text-align: center; }}
  .cac-week-arrow {{ font-size: 18px; color: #334155; padding: 0 6px; align-self: center; }}
  .cac-note {{ font-size: 11px; color: #334155; margin-top: 14px; font-style: italic; }}

  /* COST SUMMARY */
  .cost-row {{ display: flex; gap: 20px; margin-top: 32px; }}
  .cost-card {{ flex: 1; background: #0d1018; border: 1px solid #1e2535; border-radius: 10px; padding: 28px; }}
  .cost-card.highlight {{ border-color: #f59e0b44; background: #0d1018; }}
  .cost-label {{ font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: #475569; margin-bottom: 8px; }}
  .cost-value {{ font-size: 36px; font-weight: 800; color: #f59e0b; }}
  .cost-sub {{ font-size: 13px; color: #475569; margin-top: 6px; }}
  .efficiency-bar {{ margin-top: 24px; }}
  .eff-label {{ font-size: 12px; color: #64748b; margin-bottom: 8px; }}
  .eff-track {{ background: #1e2535; border-radius: 4px; height: 8px; }}
  .eff-fill {{ background: linear-gradient(90deg, #f59e0b, #34d399); border-radius: 4px; height: 8px; }}

  /* PHASE 2 VALIDATION */
  .val-cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin-bottom: 32px; }}
  @media (max-width: 800px) {{ .val-cards {{ grid-template-columns: 1fr; }} }}
  .val-card {{ background: #0d1018; border: 1px solid #1e2535; border-radius: 10px; padding: 22px; }}
  .val-card-label {{ font-size: 16px; font-weight: 700; margin-bottom: 4px; }}
  .val-card-range {{ font-size: 12px; color: #64748b; margin-bottom: 16px; }}
  .val-card-metrics {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 16px; }}
  .val-metric {{ background: #080b12; border: 1px solid #1e2535; border-radius: 6px; padding: 10px; text-align: center; }}
  .val-metric-label {{ font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: #475569; margin-bottom: 4px; }}
  .val-metric-value {{ font-size: 18px; font-weight: 800; color: #e4e7ef; }}
  .val-finding {{ font-size: 13px; color: #94a3b8; font-style: italic; line-height: 1.6; border-left: 3px solid #1e2535; padding-left: 10px; }}
  .val-insights {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
  @media (max-width: 800px) {{ .val-insights {{ grid-template-columns: 1fr; }} }}
  .val-insight-panel {{ background: #0d1018; border: 1px solid #1e2535; border-radius: 10px; padding: 22px; }}
  .val-insight-title {{ font-size: 14px; font-weight: 700; color: #e4e7ef; margin-bottom: 12px; }}
  .val-legend {{ display: flex; align-items: center; gap: 4px; font-size: 12px; color: #94a3b8; margin-bottom: 14px; }}
  .val-legend-dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  .sv-row {{ display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }}
  .sv-label {{ font-size: 12px; color: #64748b; width: 40px; flex-shrink: 0; }}
  .sv-bars {{ display: flex; flex-direction: column; gap: 4px; flex: 1; }}
  .sv-bar-wrap {{ display: flex; align-items: center; gap: 8px; }}
  .sv-bar {{ height: 10px; border-radius: 2px; display: inline-block; }}
  .sv-val {{ font-size: 12px; font-weight: 700; color: #e4e7ef; white-space: nowrap; }}
  .val-nb-note {{ font-size: 12px; color: #64748b; font-style: italic; margin-top: 14px; line-height: 1.6; }}

  footer {{ padding: 40px 0; text-align: center; font-size: 12px; color: #334155; }}
</style>
</head>
<body>

<!-- HERO -->
<div class="hero">
  <div class="container">
    <div class="hero-eyebrow">Nowa — Audience Intelligence</div>
    <h1 class="hero-title">Finding Nowa's Market<br>A Data-Driven Audience Experiment</h1>
    <p class="hero-subtitle">10 Segments &nbsp;·&nbsp; 20 Days &nbsp;·&nbsp; 1 Clear Winner Tier</p>
    <div class="hero-stats">
      <div class="hero-stat">
        <div class="hero-stat-value">${total_spend:,.0f}</div>
        <div class="hero-stat-label">Total Spend</div>
      </div>
      <div class="hero-stat">
        <div class="hero-stat-value">{total_fsd}</div>
        <div class="hero-stat-label">Total Signups (FSD)</div>
      </div>
      <div class="hero-stat">
        <div class="hero-stat-value">{total_paid}</div>
        <div class="hero-stat-label">Paid Deposits</div>
      </div>
      <div class="hero-stat">
        <div class="hero-stat-value">{overall_paid_rate:.1f}%</div>
        <div class="hero-stat-label">Paid Conversion Rate</div>
      </div>
    </div>
    <div class="date-range">May 12 – May 31, 2026</div>
  </div>
</div>

<!-- SECTION 1: THE EXPERIMENT -->
<section>
  <div class="container">
    <div class="section-label">Section 1</div>
    <h2 class="section-title">The Experiment</h2>
    <p class="section-desc">10 distinct parent personas were targeted simultaneously with equal daily budget and identical creative testing frameworks. Every 5–7 days, the lowest performers were paused — ensuring budget flowed toward proven signals, not assumptions.</p>

    <div class="elim-funnel">
      <div class="elim-stage">
        <div class="elim-num">10</div>
        <div class="elim-label">Campaigns Launched</div>
        <div class="elim-date">May 13</div>
      </div>
      <div class="elim-arrow">&#8594;</div>
      <div class="elim-stage">
        <div class="elim-num">8</div>
        <div class="elim-label">Active</div>
        <div class="elim-date">May 18</div>
        <div class="elim-cut">&#8722;2 paused</div>
      </div>
      <div class="elim-arrow">&#8594;</div>
      <div class="elim-stage">
        <div class="elim-num">5</div>
        <div class="elim-label">Active</div>
        <div class="elim-date">May 22</div>
        <div class="elim-cut">&#8722;3 paused</div>
      </div>
      <div class="elim-arrow">&#8594;</div>
      <div class="elim-stage">
        <div class="elim-num">3</div>
        <div class="elim-label">Survivors</div>
        <div class="elim-date">May 27–28</div>
        <div class="elim-cut">&#8722;2 phased out</div>
      </div>
    </div>
  </div>
</section>

<!-- SECTION 2: CAMPAIGN RANKINGS -->
<section>
  <div class="container">
    <div class="section-label">Section 2</div>
    <h2 class="section-title">Campaign Rankings — All 10</h2>
    <p class="section-desc">Ranked by form submission volume (FSD). Each card shows click quality, pipeline depth, and cost efficiency.</p>
    <div class="rank-grid">
      {cards_html}
    </div>
  </div>
</section>

<!-- SECTION 3: TOP 3 FUNNELS -->
<section>
  <div class="container">
    <div class="section-label">Section 3</div>
    <h2 class="section-title">The Funnel — Top 3 Winners</h2>
    <p class="section-desc">Full conversion funnel from ad impression to paid deposit for each surviving campaign.</p>
    <div class="funnel-grid">
      {funnel_html}
    </div>
  </div>
</section>

<!-- SECTION 4: WHY THESE 3 WON (enhanced with first-touchpoint) -->
<section>
  <div class="container">
    <div class="section-label">Section 4</div>
    <h2 class="section-title">Why These 3 Won</h2>
    <p class="section-desc">Each winner demonstrated a distinct, repeatable signal across 16 days of live traffic. The first-touchpoint line describes the audience and how they entered the funnel.</p>
    <div class="insight-grid">
      {insight_html}
    </div>
  </div>
</section>

{phase2_section_html}

<!-- SECTION 5: CAC TREND -->
<section>
  <div class="container">
    <div class="section-label">Section 5</div>
    <h2 class="section-title">CAC Trend — Week 1 &#8594; Week 3</h2>
    <p class="section-desc">Cost per acquired customer tracked week-by-week. A downward trend signals improving efficiency as the algorithm optimizes. Paid deposits are approximated proportionally from weekly FSD weight.</p>
    <div class="cac-grid">
      {cac_trend_html}
    </div>
  </div>
</section>

<!-- SECTION 6: INVESTMENT SUMMARY -->
<section>
  <div class="container">
    <div class="section-label">Section 6</div>
    <h2 class="section-title">Investment Summary</h2>
    <p class="section-desc">Total experimental investment vs. the spend concentrated on the proven winner tier.</p>
    <div class="cost-row">
      <div class="cost-card">
        <div class="cost-label">Total Experiment Spend</div>
        <div class="cost-value">${total_spend:,.2f}</div>
        <div class="cost-sub">Across all 10 segments, May 13–28</div>
      </div>
      <div class="cost-card highlight">
        <div class="cost-label">Winner Tier Spend (Top 3)</div>
        <div class="cost-value">${top3_spend:,.2f}</div>
        <div class="cost-sub">{pct(top3_spend, total_spend):.0f}% of budget — {total_paid} paid deposits, {sum(stripe[c[0]]['paid'] for c in CAMPAIGNS[:3])} from top 3</div>
      </div>
      <div class="cost-card">
        <div class="cost-label">Learning Budget (Cut campaigns)</div>
        <div class="cost-value">${cut_spend:,.2f}</div>
        <div class="cost-sub">Eliminated before wasting further — market signal acquired</div>
      </div>
    </div>
    <div class="efficiency-bar" style="margin-top:32px">
      <div class="eff-label">Winner tier share of total spend</div>
      <div class="eff-track">
        <div class="eff-fill" style="width:{pct(top3_spend, total_spend):.0f}%"></div>
      </div>
      <div style="font-size:12px;color:#475569;margin-top:6px">{pct(top3_spend, total_spend):.0f}% of spend · {pct(sum(stripe[c[0]]['paid'] for c in CAMPAIGNS[:3]), total_paid):.0f}% of all paid deposits</div>
    </div>
  </div>
</section>

<footer>
  <div class="container">
    Nowa &nbsp;·&nbsp; Meta Ads Campaign Test &nbsp;·&nbsp; May 2026 &nbsp;·&nbsp; Internal Use
  </div>
</footer>
</body>
</html>"""

    out = REPORTS_DIR / "investor_report.html"
    out.write_text(html, encoding="utf-8")
    print(f"Written: {out}")
    return str(out)


# ============================================================================
# ============================================================================
# RETRO REPORT — SECTION BUILDERS
# ============================================================================
# ============================================================================

def _build_retro_s6_customer_profiles(stripe_seg, seg_agg, weekly_seg):
    """
    Section 6: Customer Profile per Segment.
    Shows primary audience, gender/age/country signals, and scalability signal
    (Week 1 vs Week 2 FSD efficiency). Uses segment totals from Meta API.
    """
    # Hardcoded demographic signals from demographics report (gender/age/country)
    demo_signals = {
        "Nostalgia Bridge Dad": {
            "gender": "~80% Male",
            "top_age": "35–44",
            "top_country": "United States",
            "audience_desc": "Fathers aged 35–44, US-centric, resonating with nostalgic emotional messaging",
        },
        "Sturdy Parenting": {
            "gender": "~60% Female",
            "top_age": "30–44",
            "top_country": "United States",
            "audience_desc": "Primarily mothers aged 30–44 seeking structured, evidence-based parenting frameworks",
        },
        "Routine-Chaos": {
            "gender": "~65% Female",
            "top_age": "25–44",
            "top_country": "United States",
            "audience_desc": "Parents aged 25–44 overwhelmed by daily household chaos, skewing female",
        },
    }

    cards_html = ""
    for seg_name, color in [
        ("Nostalgia Bridge Dad", "#f59e0b"),
        ("Sturdy Parenting", "#34d399"),
        ("Routine-Chaos", "#f472b6"),
    ]:
        paid_total = stripe_seg.get(seg_name, {}).get("paid", 0)
        demo = demo_signals[seg_name]
        a = seg_agg.get(seg_name, {})
        spend = a.get("total_spend", 0.0)
        fsd   = a.get("total_fsd", 0.0)
        cpm   = a.get("avg_cpm", 0.0)

        # FSD efficiency from weekly segment data
        weekly_tuples = weekly_seg.get(seg_name, [])
        w1_spend = w1_fsd = w2_spend = w2_fsd = 0.0
        for week_label, w_spend, w_fsd, _, _ in weekly_tuples:
            if "May 13" in week_label or "Week 1" in week_label:
                w1_spend, w1_fsd = w_spend, w_fsd
            elif "May 20" in week_label or "Week 2" in week_label:
                w2_spend, w2_fsd = w_spend, w_fsd

        eff1 = w1_fsd / w1_spend if w1_spend > 0 else 0
        eff2 = w2_fsd / w2_spend if w2_spend > 0 else 0

        if eff2 > eff1 * 1.05:
            scale_verdict = "Improving"
            scale_color = "#34d399"
            scale_icon = "&#8593;"
            scale_note = "FSD efficiency grew Week 1 → Week 2 — scalable signal."
        elif eff2 < eff1 * 0.95:
            scale_verdict = "Saturating"
            scale_color = "#f87171"
            scale_icon = "&#8595;"
            scale_note = "FSD efficiency declined Week 1 → Week 2 — watch for audience fatigue."
        else:
            scale_verdict = "Stable"
            scale_color = "#94a3b8"
            scale_icon = "&#8596;"
            scale_note = "FSD efficiency held steady — consistent but not accelerating."

        eff1_str = f"{eff1:.3f}" if eff1 else "—"
        eff2_str = f"{eff2:.3f}" if eff2 else "—"

        cards_html += f"""
        <div class="profile-card" style="border-top:3px solid {color}">
          <div class="profile-header" style="color:{color}">{seg_name}</div>
          <div class="profile-desc">{demo['audience_desc']}</div>
          <div class="profile-row">
            <div class="profile-item">
              <div class="profile-item-label">Primary Gender</div>
              <div class="profile-item-val">{demo['gender']}</div>
            </div>
            <div class="profile-item">
              <div class="profile-item-label">Top Age Bracket</div>
              <div class="profile-item-val">{demo['top_age']}</div>
            </div>
            <div class="profile-item">
              <div class="profile-item-label">Top Country</div>
              <div class="profile-item-val">{demo['top_country']}</div>
            </div>
            <div class="profile-item">
              <div class="profile-item-label">Paid Deposits</div>
              <div class="profile-item-val" style="color:{color}">{paid_total}</div>
            </div>
            <div class="profile-item">
              <div class="profile-item-label">Total Spend (Segment)</div>
              <div class="profile-item-val">${spend:,.2f}</div>
            </div>
            <div class="profile-item">
              <div class="profile-item-label">Total FSD (Segment)</div>
              <div class="profile-item-val">{int(fsd)}</div>
            </div>
            <div class="profile-item">
              <div class="profile-item-label">Avg CPM</div>
              <div class="profile-item-val">${cpm:.2f}</div>
            </div>
            <div class="profile-item">
              <div class="profile-item-label">CPaC</div>
              <div class="profile-item-val" style="color:{color}">{fmt_cpac(cpac(spend, paid_total))}</div>
            </div>
          </div>
          <div class="profile-scale" style="border:1px solid {scale_color}33;background:{scale_color}0d">
            <div class="profile-scale-badge" style="color:{scale_color}">
              <span style="font-size:16px;">{scale_icon}</span> {scale_verdict}
            </div>
            <div class="profile-scale-detail">
              W1 efficiency: {eff1_str} FSD/$ &nbsp;·&nbsp; W2 efficiency: {eff2_str} FSD/$
            </div>
            <div class="profile-scale-note">{scale_note}</div>
          </div>
        </div>"""

    return f"""
<section>
  <div class="container">
    <div class="section-title">Section 6 &mdash; Customer Profile per Segment</div>
    <div class="profile-grid">
      {cards_html}
    </div>
    <div class="note">Spend, FSD, and CPM are segment totals from Meta API (adset-level, all campaigns). Demographic signals from Meta Insights API. Scalability signal = FSD/spend ratio Week 1 vs Week 2.</div>
  </div>
</section>"""


def _build_retro_s7_ad_format_verdict(seg_ad_perf, extra):
    """
    Section 7: Ad Format & Angle Verdict.
    Groups ad performance by format and style, assigns winner/cut/inconclusive verdict.
    Uses live segment ad performance data (all campaigns incl. LEADS).
    Falls back to extra.json data if live data is empty.
    """
    ad_perf = seg_ad_perf if seg_ad_perf else extra.get("ad_performance", [])
    if not ad_perf:
        return ""

    # Group by format + style (collapse campaign_id for aggregate view)
    from collections import defaultdict
    grouped = defaultdict(lambda: {"ctr_sum": 0, "cpc_sum": 0, "fsd_sum": 0, "count": 0, "ads": []})
    for ad in ad_perf:
        key = (ad["ad_format"] or "unknown", ad["ad_style"] or "unknown")
        g = grouped[key]
        g["ctr_sum"] += (ad["avg_ctr"] or 0)
        g["cpc_sum"] += (ad["avg_cpc"] or 0)
        g["fsd_sum"] += (ad["total_fsd"] or 0)
        g["count"] += 1
        g["ads"].append(ad)

    # Compute averages
    rows = []
    for (fmt, style), g in grouped.items():
        n = g["count"]
        avg_ctr = g["ctr_sum"] / n if n > 0 else 0
        avg_cpc = g["cpc_sum"] / n if n > 0 else 0
        total_fsd = g["fsd_sum"]
        rows.append((fmt, style, avg_ctr, avg_cpc, total_fsd))

    # Sort by total_fsd desc
    rows.sort(key=lambda x: -x[4])

    # Compute medians for verdict logic
    fsds = [r[4] for r in rows]
    ctrs = [r[2] for r in rows]
    med_fsd = median(fsds) if fsds else 0
    med_ctr = median(ctrs) if ctrs else 0
    p25_fsd = sorted(fsds)[len(fsds) // 4] if fsds else 0

    def verdict(fsd, ctr):
        if fsd > med_fsd and ctr > med_ctr:
            return '<span class="v-winner">&#10003; Winner</span>'
        elif fsd < p25_fsd and ctr < med_ctr:
            return '<span class="v-cut">&#10007; Cut</span>'
        else:
            return '<span class="v-inconclusive">&#9888; Inconclusive</span>'

    table_rows = ""
    for fmt, style, avg_ctr, avg_cpc, total_fsd in rows:
        fmt_display = fmt.replace("_", " ").title()
        style_display = style.replace("_", " ").title()
        table_rows += f"""
        <tr>
          <td><span class="format-chip format-{fmt}">{fmt_display}</span></td>
          <td>{style_display}</td>
          <td>{avg_ctr:.2f}%</td>
          <td>${avg_cpc:.3f}</td>
          <td>{int(total_fsd)}</td>
          <td>{verdict(total_fsd, avg_ctr)}</td>
        </tr>"""

    return f"""
<section>
  <div class="container">
    <div class="section-title">Section 7 &mdash; Ad Format &amp; Angle Verdict</div>
    <p class="note" style="margin-bottom:16px;">Aggregated across all ad-level data in all 13 campaigns (SALES + LEADS). Includes LEADS campaign video and static ads attributed to their segments. Verdict: &#10003; Winner = FSD &gt; median AND CTR &gt; median. &#10007; Cut = FSD &lt; 25th percentile AND CTR &lt; median.</p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Format</th>
            <th>Style / Angle</th>
            <th>Avg CTR</th>
            <th>Avg CPC</th>
            <th>Total FSD</th>
            <th>Verdict</th>
          </tr>
        </thead>
        <tbody>
          {table_rows}
        </tbody>
      </table>
    </div>
    <div class="note">CTR and CPC are averages across ads in each format/style group. FSD = total form submissions.</div>
  </div>
</section>"""


def _build_retro_s8_device_breakdown(extra):
    """
    Section 8: Desktop vs Mobile breakdown per campaign.
    Two-column comparison table per campaign.
    """
    device_data = extra.get("device_breakdown", {})
    if not device_data:
        return ""

    sections_html = ""
    for name, color in [
        ("Nostalgia Bridge Dad", "#f59e0b"),
        ("Sturdy Parenting", "#34d399"),
        ("Routine-Chaos", "#f472b6"),
    ]:
        cid = NAME_TO_ID[name]
        rows_raw = device_data.get(cid, [])

        # Group: desktop vs mobile (combine mobile_app + mobile_web)
        device_totals = {}
        for r in rows_raw:
            dp = r.get("device_platform", "unknown")
            spend = float(r.get("spend", 0) or 0)
            impr = int(r.get("impressions", 0) or 0)
            clicks = int(r.get("clicks", 0) or 0)
            ctr = float(r.get("ctr", 0) or 0)
            cpc = float(r.get("cpc", 0) or 0)

            if dp in ("mobile_app", "mobile_web"):
                bucket = "Mobile"
            elif dp == "desktop":
                bucket = "Desktop"
            else:
                continue  # skip unknown

            if bucket not in device_totals:
                device_totals[bucket] = {
                    "spend": 0, "impressions": 0, "clicks": 0,
                    "ctr_weighted": 0, "cpc_weighted": 0
                }
            device_totals[bucket]["spend"] += spend
            device_totals[bucket]["impressions"] += impr
            device_totals[bucket]["clicks"] += clicks
            device_totals[bucket]["ctr_weighted"] += ctr * spend
            device_totals[bucket]["cpc_weighted"] += cpc * spend

        total_spend_all = sum(d["spend"] for d in device_totals.values())

        table_rows = ""
        winner_device = None
        winner_ctr = -1
        for bucket in ["Mobile", "Desktop"]:
            d = device_totals.get(bucket)
            if not d:
                continue
            spend_pct = pct(d["spend"], total_spend_all)
            avg_ctr = d["ctr_weighted"] / d["spend"] if d["spend"] > 0 else 0
            avg_cpc = d["cpc_weighted"] / d["spend"] if d["spend"] > 0 else 0
            if avg_ctr > winner_ctr:
                winner_ctr = avg_ctr
                winner_device = bucket
            table_rows += f"""
            <tr>
              <td style="font-weight:600;color:{color if bucket=='Mobile' else '#94a3b8'}">{bucket}</td>
              <td>${d['spend']:,.2f} ({spend_pct:.0f}%)</td>
              <td>{avg_ctr:.2f}%</td>
              <td>${avg_cpc:.3f}</td>
            </tr>"""

        verdict_txt = f"{winner_device} performs better (higher CTR)" if winner_device else "—"
        sections_html += f"""
        <div class="device-block" style="border-top:3px solid {color}">
          <div class="device-block-title" style="color:{color}">{name}</div>
          <div class="table-wrap" style="margin-top:12px">
            <table>
              <thead>
                <tr>
                  <th>Device</th>
                  <th>Spend (% of total)</th>
                  <th>Avg CTR</th>
                  <th>Avg CPC</th>
                </tr>
              </thead>
              <tbody>
                {table_rows}
              </tbody>
            </table>
          </div>
          <div class="device-verdict">Verdict: <strong>{verdict_txt}</strong></div>
        </div>"""

    return f"""
<section>
  <div class="container">
    <div class="section-title">Section 8 &mdash; Desktop vs Mobile</div>
    <p class="note" style="margin-bottom:24px;">Device platform breakdown for each top 3 campaign. Mobile includes mobile_app + mobile_web. Data: Meta API May 12–31.</p>
    <div class="device-grid">
      {sections_html}
    </div>
  </div>
</section>"""


def _build_retro_s9_anomalies(seg_daily_cpr, extra):
    """
    Section 9: Time Series Anomalies.
    Flags days where segment-level CPR (cost per FSD) was >50% above or <50% below average.
    Shows top 3 anomalies per segment.
    Uses live segment daily CPR data; falls back to extra.json campaign-level data.
    """
    # Prefer live segment data; fall back to extra.json
    use_seg = bool(seg_daily_cpr)
    if not use_seg:
        daily_cpr_raw = extra.get("daily_cpr", [])
        if not daily_cpr_raw:
            return ""
        by_camp = defaultdict(list)
        for row in daily_cpr_raw:
            if row["cpr"] is not None:
                by_camp[row["campaign_id"]].append(row)

    sections_html = ""
    for name, color in [
        ("Nostalgia Bridge Dad", "#f59e0b"),
        ("Sturdy Parenting", "#34d399"),
        ("Routine-Chaos", "#f472b6"),
    ]:
        if use_seg:
            rows = [r for r in seg_daily_cpr.get(name, []) if r["cpr"] is not None]
        else:
            cid = NAME_TO_ID[name]
            rows = by_camp.get(cid, [])
        if not rows:
            continue

        avg_cpr = sum(r["cpr"] for r in rows) / len(rows)

        # Find anomalies: >50% above or <50% below average
        anomalies = []
        for r in rows:
            ratio = (r["cpr"] - avg_cpr) / avg_cpr if avg_cpr > 0 else 0
            if ratio > 0.5:
                note = "High CPR day"
                atype = "high"
            elif ratio < -0.5:
                note = "Low CPR day"
                atype = "low"
            else:
                continue
            anomalies.append({
                "date": r["date"],
                "cpr": r["cpr"],
                "ratio": ratio,
                "note": note,
                "atype": atype,
            })

        # Sort by absolute deviation, take top 3
        anomalies.sort(key=lambda x: -abs(x["ratio"]))
        top_anomalies = anomalies[:3]

        if not top_anomalies:
            table_html = '<tr><td colspan="4" style="color:#475569;padding:14px;text-align:center;">No anomalies detected (all days within ±50% of average)</td></tr>'
        else:
            table_html = ""
            for a in top_anomalies:
                ratio_pct = a["ratio"] * 100
                ratio_str = f"+{ratio_pct:.0f}%" if ratio_pct > 0 else f"{ratio_pct:.0f}%"
                ratio_color = "#f87171" if a["atype"] == "high" else "#34d399"
                table_html += f"""
                <tr>
                  <td>{a['date']}</td>
                  <td>${a['cpr']:.2f}</td>
                  <td style="color:{ratio_color};font-weight:700">{ratio_str}</td>
                  <td>{a['note']}</td>
                </tr>"""

        avg_str = f"${avg_cpr:.2f}"
        sections_html += f"""
        <div class="anomaly-block" style="border-top:2px solid {color}33;padding-top:20px;margin-bottom:28px">
          <div class="anomaly-title" style="color:{color}">{name} &nbsp;<span style="color:#475569;font-size:12px;font-weight:400">Avg CPR: {avg_str}</span></div>
          <div class="table-wrap" style="margin-top:10px">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>CPR</th>
                  <th>vs Avg</th>
                  <th>Note</th>
                </tr>
              </thead>
              <tbody>
                {table_html}
              </tbody>
            </table>
          </div>
        </div>"""

    return f"""
<section>
  <div class="container">
    <div class="section-title">Section 9 &mdash; Time Series Anomalies</div>
    <p class="note" style="margin-bottom:24px;">Days where segment CPR (cost per FSD) deviated &gt;50% from the segment average. Top 3 anomalies per segment shown. Segment CPR = sum of daily spend across all segment ad sets (SALES + LEADS) &#247; sum of daily FSD. Days with 0 FSD excluded.</p>
    {sections_html}
  </div>
</section>"""


def _build_retro_s10_exclusion_plan(stripe):
    """
    Section 10: Audience Exclusion Plan.
    Recommends excluding already-touched audiences from next campaign.
    """
    # Top 3 totals
    nb_paid   = stripe["Nostalgia Bridge Dad"]["paid"]
    nb_pend   = stripe["Nostalgia Bridge Dad"]["pending"]
    sp_paid   = stripe["Sturdy Parenting"]["paid"]
    sp_pend   = stripe["Sturdy Parenting"]["pending"]
    rc_paid   = stripe["Routine-Chaos"]["paid"]
    rc_pend   = stripe["Routine-Chaos"]["pending"]

    total_paid = sum(stripe[n]["paid"] for n in stripe)
    total_pend = sum(stripe[n]["pending"] for n in stripe)

    table_rows = f"""
        <tr>
          <td style="font-weight:600;color:#f87171">Paid customers</td>
          <td>All 10 campaigns</td>
          <td style="color:#34d399;font-weight:700">{total_paid}</td>
          <td>Already converted — retargeting wastes budget; move to retention/upsell flow instead</td>
          <td><span class="excl-badge excl-yes">Exclude</span></td>
        </tr>
        <tr>
          <td style="font-weight:600;color:#f59e0b">Form submitters (pending)</td>
          <td>All 10 campaigns</td>
          <td style="color:#f59e0b;font-weight:700">{total_pend}</td>
          <td>Touched the funnel, did not pay — exclude to avoid re-spending on cold leads; consider separate low-budget nudge campaign</td>
          <td><span class="excl-badge excl-nudge">Nudge only</span></td>
        </tr>
        <tr>
          <td style="font-weight:600;color:#60a5fa">Paid — Top 3 only</td>
          <td>Nostalgia Bridge, Sturdy Parenting, Routine-Chaos</td>
          <td style="color:#34d399;font-weight:700">{nb_paid + sp_paid + rc_paid}</td>
          <td>Build a lookalike of these {nb_paid + sp_paid + rc_paid} paid users for next campaign scale-up — highest signal quality available</td>
          <td><span class="excl-badge excl-look">Lookalike Source</span></td>
        </tr>
        <tr>
          <td style="font-weight:600;color:#94a3b8">Form submitters — Top 3 (pending)</td>
          <td>Nostalgia Bridge, Sturdy Parenting, Routine-Chaos</td>
          <td style="color:#f59e0b;font-weight:700">{nb_pend + sp_pend + rc_pend}</td>
          <td>Already filled form but not paid — exclude from main acquisition; consider re-engagement with discounted entry or urgency prompt</td>
          <td><span class="excl-badge excl-nudge">Nudge only</span></td>
        </tr>"""

    return f"""
<section>
  <div class="container">
    <div class="section-title">Section 10 &mdash; Audience Exclusion Plan</div>
    <p class="note" style="margin-bottom:16px;">Before scaling up the top 3 campaigns, exclude already-touched audiences to avoid wasting budget on users who cannot convert again.</p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Audience Segment</th>
            <th>Source Campaigns</th>
            <th>Count</th>
            <th>Rationale</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {table_rows}
        </tbody>
      </table>
    </div>
    <div class="note" style="margin-top:12px;">Stripe paid = confirmed deposit. Pending = form submitted but payment not completed. Counts are from Stripe export as of May 29, 2026.</div>
  </div>
</section>"""


def _build_retro_s11_scaleup(stripe_seg):
    """
    Section 11: Scale-up Recommendations.
    3-card layout, one per winning segment. CPaC numbers are segment-level totals.
    """
    nb_paid = stripe_seg.get("Nostalgia Bridge Dad", {}).get("paid", 0)
    sp_paid = stripe_seg.get("Sturdy Parenting", {}).get("paid", 0)
    rc_paid = stripe_seg.get("Routine-Chaos", {}).get("paid", 0)
    nb_pend = stripe_seg.get("Nostalgia Bridge Dad", {}).get("pending", 0)
    sp_pend = stripe_seg.get("Sturdy Parenting", {}).get("pending", 0)
    rc_pend = stripe_seg.get("Routine-Chaos", {}).get("pending", 0)

    # Segment-level CPaC (from spec: Nostalgia $28.88 / 92 FSD / 42 paid,
    # Sturdy $27.21 lowest / 86 FSD / 43 paid, Routine $38.47 / 57 FSD / 28 paid)
    nb_cpac_str = "$28.88"
    sp_cpac_str = "$27.21"
    rc_cpac_str = "$38.47"
    nb_fsd_seg  = 92
    sp_fsd_seg  = 86
    rc_fsd_seg  = 57

    sp_paid_rate = pct(sp_paid, sp_fsd_seg)

    cards = [
        {
            "name": "Sturdy Parenting",
            "color": "#34d399",
            "icon": "&#128200;",
            "headline": "Scale first — lowest CPaC in the cohort",
            "cpac": sp_cpac_str,
            "paid": sp_paid,
            "fsd_seg": sp_fsd_seg,
            "reason": f"Lowest segment CPaC ({sp_cpac_str}) across all 10 audiences — more efficient per acquired customer than even Nostalgia Bridge Dad when LEADS campaigns are included. High FSD-to-paid rate ({sp_paid_rate:.0f}%) confirms genuine purchase intent at scale.",
            "actions": [
                f"Build 1% lookalike of the {sp_paid} Sturdy Parenting paid customers",
                "Double daily budget — segment efficiency warrants it",
                "Test 'social proof / results' ad angle to complement product_hero winner",
                "Monitor FSD-to-paid rate — if it holds above 50%, scale aggressively",
            ],
            "confidence": "High",
            "conf_color": "#34d399",
        },
        {
            "name": "Nostalgia Bridge Dad",
            "color": "#f59e0b",
            "icon": "&#128640;",
            "headline": "Scale 2x budget — second most efficient",
            "cpac": nb_cpac_str,
            "paid": nb_paid,
            "fsd_seg": nb_fsd_seg,
            "reason": f"Segment CPaC {nb_cpac_str} with the strongest CTR (11%+). The emotional nostalgia angle has not shown fatigue — this segment has the clearest path to efficient volume at scale.",
            "actions": [
                f"Double daily budget (from ~$45/day to ~$90/day)",
                "Test 2–3 new ad variations on the nostalgia/emotional angle",
                f"Build a lookalike audience from the {nb_paid} paid customers",
                f"Exclude existing {nb_paid + nb_pend} touched users",
            ],
            "confidence": "High",
            "conf_color": "#34d399",
        },
        {
            "name": "Routine-Chaos",
            "color": "#f472b6",
            "icon": "&#128300;",
            "headline": "Test new angles to reduce CPaC",
            "cpac": rc_cpac_str,
            "paid": rc_paid,
            "fsd_seg": rc_fsd_seg,
            "reason": f"Segment CPaC {rc_cpac_str} is higher than Sturdy and Nostalgia. Consistent delivery is a strength, but improving ad creative efficiency before scaling will maximize return. The contrast_repositioning angle is the clear winner — double down.",
            "actions": [
                "Keep current budget — do not scale until CPaC approaches $28",
                "Launch 2–3 new contrast_repositioning variations (the top-performing angle)",
                "A/B test 'before/after routine' vs 'chaos → calm' narrative",
                "If new angle hits CPaC < $30, then scale to 2x budget",
            ],
            "confidence": "Medium",
            "conf_color": "#f59e0b",
        },
    ]

    cards_html = ""
    for c in cards:
        action_items = "".join(
            f'<li style="color:#94a3b8;font-size:13px;line-height:1.7;margin-bottom:4px;">{a}</li>'
            for a in c["actions"]
        )
        cards_html += f"""
        <div class="scale-card" style="border-top:3px solid {c['color']}">
          <div class="scale-header">
            <span class="scale-icon">{c['icon']}</span>
            <div>
              <div class="scale-name" style="color:{c['color']}">{c['name']}</div>
              <div class="scale-headline">{c['headline']}</div>
            </div>
            <span class="scale-confidence" style="color:{c['conf_color']};background:{c['conf_color']}18;border:1px solid {c['conf_color']}44">{c['confidence']} confidence</span>
          </div>
          <div class="scale-kpis">
            <div class="scale-kpi">
              <div class="scale-kpi-label">Segment CPaC</div>
              <div class="scale-kpi-val" style="color:{c['color']}">{c['cpac']}</div>
            </div>
            <div class="scale-kpi">
              <div class="scale-kpi-label">Total FSD (Segment)</div>
              <div class="scale-kpi-val">{c['fsd_seg']}</div>
            </div>
            <div class="scale-kpi">
              <div class="scale-kpi-label">Paid Deposits</div>
              <div class="scale-kpi-val">{c['paid']}</div>
            </div>
          </div>
          <p class="scale-reason">{c['reason']}</p>
          <div class="scale-actions-label">Recommended actions:</div>
          <ul class="scale-actions">
            {action_items}
          </ul>
        </div>"""

    return f"""
<section>
  <div class="container">
    <div class="section-title">Section 11 &mdash; Scale-up Recommendations</div>
    <p class="note" style="margin-bottom:24px;">Actionable next steps for each winning segment based on segment-level CPaC (SALES + LEADS combined), paid conversion rate, and efficiency trend. Sturdy Parenting is now ranked first — lowest CPaC in the cohort at the segment level.</p>
    <div class="scale-grid">
      {cards_html}
    </div>
  </div>
</section>"""


def _build_retro_s12_format_validation():
    """
    Section 12: Format Validation — Static vs Video.
    Hardcoded data from May 26-31 validation campaigns.
    """
    # Overall comparison table
    overall_rows = [
        ("Avg CTR",       "8.02%", "5.22%", "Static"),
        ("Avg CPC",       "$0.53",  "$1.03", "Static"),
        ("Avg CPM",       "$30.16", "$48.19", "Static"),
        ("FSD per $100",  "6.7",   "6.2",   "Static"),
    ]
    overall_html = ""
    for metric, static_val, video_val, winner in overall_rows:
        overall_html += f"""
        <tr>
          <td>{metric}</td>
          <td style="color:#34d399;font-weight:700">{static_val}</td>
          <td style="color:#f59e0b">{video_val}</td>
          <td><span style="color:#34d399;font-weight:700">{winner} &#10003;</span></td>
        </tr>"""

    # Per-segment breakdown table
    segment_rows = [
        ("Nostalgia Bridge", 13, "$0.22", 8, "$0.78", "#f59e0b"),
        ("Sturdy Parenting", 12, "$0.27", 7, "$0.89", "#34d399"),
        ("Routine-Chaos",    11, "$0.42", 6, "$1.13", "#f472b6"),
    ]
    segment_html = ""
    for seg, s_fsd, s_cpc, v_fsd, v_cpc, color in segment_rows:
        segment_html += f"""
        <tr>
          <td style="color:{color};font-weight:600">{seg}</td>
          <td style="color:#34d399;font-weight:700">{s_fsd}</td>
          <td>{s_cpc}</td>
          <td style="color:#f59e0b">{v_fsd}</td>
          <td>{v_cpc}</td>
        </tr>"""

    return f"""
<section>
  <div class="container">
    <div class="section-title">Section 12 &mdash; Format Validation &mdash; Static vs Video</div>
    <p class="note" style="margin-bottom:16px;">Validation phase May 28&ndash;31. Static: $525.64 spend / 35 FSD (6.7 FSD/$100). Video: $324.35 spend / 20 FSD (6.2 FSD/$100).</p>

    <!-- Methodology note -->
    <div style="background:#0d1018;border:1px solid #2a2e3a;border-left:4px solid #818cf8;
                border-radius:0 8px 8px 0;padding:16px 20px;margin-bottom:24px;">
      <div style="color:#818cf8;font-size:11px;text-transform:uppercase;letter-spacing:.08em;
                  font-weight:700;margin-bottom:10px;">Why These Campaigns Were Separated &amp; How to Read This Comparison</div>
      <p style="color:#cbd5e1;font-size:13px;line-height:1.8;margin:0 0 10px;">
        <strong style="color:#e4e7ef;">Delivery bias problem:</strong> When static and video ads ran in the same ad set,
        Meta&rsquo;s algorithm heavily favoured static ads, starving videos of impressions. The video campaign was
        separated into its own dedicated campaign to give each format a fair share of delivery.
      </p>
      <p style="color:#cbd5e1;font-size:13px;line-height:1.8;margin:0 0 12px;">
        <strong style="color:#e4e7ef;">Duration bias problem:</strong> The video campaign ran for only <strong style="color:#f59e0b;">4 days</strong>
        vs. the static campaign running longer, so raw totals (total FSD, total spend) are naturally lower for video
        &mdash; not because it performs worse, but because it had less time. Comparing raw totals is unfair.
      </p>
      <div style="background:#1a1d27;border-radius:6px;padding:12px 16px;">
        <div style="color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;">
          Fair comparison: use rate-based metrics only
        </div>
        <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;text-align:center;">
          <div>
            <div style="color:#64748b;font-size:10px;margin-bottom:3px;">FSD / $100</div>
            <div style="color:#34d399;font-size:11px;font-weight:600;">Conversion efficiency</div>
          </div>
          <div>
            <div style="color:#64748b;font-size:10px;margin-bottom:3px;">CTR %</div>
            <div style="color:#34d399;font-size:11px;font-weight:600;">Scroll-stop resonance</div>
          </div>
          <div>
            <div style="color:#64748b;font-size:10px;margin-bottom:3px;">CPC $</div>
            <div style="color:#34d399;font-size:11px;font-weight:600;">Click cost</div>
          </div>
          <div>
            <div style="color:#64748b;font-size:10px;margin-bottom:3px;">CPM $</div>
            <div style="color:#34d399;font-size:11px;font-weight:600;">Delivery cost</div>
          </div>
          <div>
            <div style="color:#f87171;font-size:10px;margin-bottom:3px;">Total FSD / Spend</div>
            <div style="color:#f87171;font-size:11px;font-weight:600;">Do NOT compare directly</div>
          </div>
        </div>
      </div>
    </div>

    <div style="margin-bottom:28px">
      <div style="font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px">Overall Format Comparison</div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Metric</th>
              <th style="color:#34d399">Static</th>
              <th style="color:#f59e0b">Video</th>
              <th>Winner</th>
            </tr>
          </thead>
          <tbody>
            {overall_html}
          </tbody>
        </table>
      </div>
    </div>

    <div style="margin-bottom:20px">
      <div style="font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px">Per-Segment Breakdown</div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Segment</th>
              <th style="color:#34d399">Static FSD</th>
              <th style="color:#34d399">Static CPC</th>
              <th style="color:#f59e0b">Video FSD</th>
              <th style="color:#f59e0b">Video CPC</th>
            </tr>
          </thead>
          <tbody>
            {segment_html}
          </tbody>
        </table>
      </div>
    </div>

    <div style="background:#0d1018;border:1px solid #1e2535;border-left:4px solid #34d399;border-radius:0 8px 8px 0;padding:16px 20px;font-size:13px;color:#cbd5e1;line-height:1.7">
      <strong style="color:#34d399">Verdict:</strong> Static wins comprehensively. Video generates FSD but at 3&times; the cost per click.
      Recommend: allocate 70% budget to static, 30% to video for brand/awareness.
      Nostalgia video is the only format where video is close to competitive (CTR 7.66%).
    </div>
    <div class="note">Confirmed top static angles by FSD: testimonial, native_ui, product_hero, transformation_proof. Note: these validation campaign ad sets are counted within their respective segments in the Segment Metrics table (Section 1), contributing to each segment&rsquo;s total spend, FSD, and CPaC.</div>
  </div>
</section>"""


def _build_retro_s13_placement_pc():
    """
    Section 13: Placement — PC vs Mobile.
    Hardcoded data from the PC-only validation campaign (May 26-31).
    """
    # Summary comparison table
    placement_rows = [
        ("CTR",        "7.39% (PC avg)", "8.02% (Top Static avg)", "PC clicks but at lower rate"),
        ("FSD/$100",   "2.8",            "6.7",                     "Mobile converts 139% better"),
        ("Nostalgia CTR", "7.06%",       "9.04%",                   "Consistent winner on PC too"),
    ]
    placement_html = ""
    for metric, pc_val, all_val, finding in placement_rows:
        placement_html += f"""
        <tr>
          <td style="font-weight:600">{metric}</td>
          <td style="color:#818cf8">{pc_val}</td>
          <td style="color:#34d399">{all_val}</td>
          <td style="color:#94a3b8;font-size:12px">{finding}</td>
        </tr>"""

    # Per-campaign PC results
    pc_campaigns = [
        ("Nostalgia Bridge", "$74.53", "7.06%", "$0.26", 4, "#f59e0b"),
        ("Routine-Chaos",    "$74.88", "4.79%", "$0.25", 2, "#f472b6"),
        ("Sturdy Parenting", "$74.29", "4.27%", "$0.25", 2, "#34d399"),
    ]
    pc_html = ""
    for seg, spend, ctr, cpc, fsd, color in pc_campaigns:
        pc_html += f"""
        <tr>
          <td style="color:{color};font-weight:600">{seg}</td>
          <td>{spend}</td>
          <td>{ctr}</td>
          <td>{cpc}</td>
          <td style="font-weight:700">{fsd}</td>
        </tr>"""

    return f"""
<section>
  <div class="container">
    <div class="section-title">Section 13 &mdash; Placement &mdash; PC vs Mobile</div>
    <p class="note" style="margin-bottom:20px;">PC-only campaign ran May 26&ndash;31 across all 3 top segments. $144.09 total spend, 4 FSD. Comparison: PC-only vs all-placements (Top Static results).</p>

    <div style="margin-bottom:28px">
      <div style="font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px">Placement Efficiency Comparison</div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Metric</th>
              <th style="color:#818cf8">PC Only</th>
              <th style="color:#34d399">All Placements (Static)</th>
              <th>Finding</th>
            </tr>
          </thead>
          <tbody>
            {placement_html}
          </tbody>
        </table>
      </div>
    </div>

    <div style="margin-bottom:20px">
      <div style="font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px">PC Campaign Results — Per Segment</div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Campaign</th>
              <th>Spend</th>
              <th>CTR</th>
              <th>CPC</th>
              <th>FSD</th>
            </tr>
          </thead>
          <tbody>
            {pc_html}
          </tbody>
        </table>
      </div>
    </div>

    <div style="background:#0d1018;border:1px solid #1e2535;border-left:4px solid #818cf8;border-radius:0 8px 8px 0;padding:16px 20px;font-size:13px;color:#cbd5e1;line-height:1.7">
      <strong style="color:#818cf8">Verdict:</strong> PC users click but don&rsquo;t submit the form.
      Mobile is the primary conversion channel. For next campaign: mobile-first creatives, PC as retargeting only.
    </div>
    <div class="note">PC FSD rate: 3.6 per $100 vs 6.4 per $100 on all-placements static &mdash; mobile converts 78% better per dollar.</div>
  </div>
</section>"""


# ============================================================================
# RETRO REPORT — MAIN BUILDER
# ============================================================================
def build_retro_report(stripe_seg, weekly_seg, extra, seg_agg, seg_daily_cpr, seg_ad_perf):
    """
    stripe_seg  : {segment_name: {paid, pending, total}} — from load_stripe_by_segment()
    weekly_seg  : {segment_name: [(week_label, spend, fsd, ctr, cpc)]} — from load_weekly_segment_data()
    extra       : dict from load_extra_data() (device breakdown etc.)
    seg_agg     : {segment_name: agg_dict} — from load_segment_data()
    seg_daily_cpr: {segment_name: [{date, spend, fsd, cpr}]} — from load_segment_daily_cpr()
    seg_ad_perf : [{ad_format, ad_style, avg_ctr, avg_cpc, total_fsd}] — from load_segment_ad_performance()
    """
    # Also keep the old stripe dict (campaign-keyed) for sections that still need it
    # Build a campaign-keyed stripe from seg stripe for backward compat in section 4
    stripe = {}
    for source, cname in SOURCE_TO_CAMPAIGN.items():
        seg = STRIPE_SOURCE_MAP_SEG.get(source)
        if seg and seg in stripe_seg:
            stripe[cname] = stripe_seg[seg]
        else:
            stripe[cname] = {"paid": 0, "pending": 0, "total": 0}

    # ----------------------------------------------------------------
    # Section 1: All 10 Audience Segments — Full Metrics Table
    # ----------------------------------------------------------------
    sorted_segs = sorted(seg_agg.keys(), key=lambda s: -seg_agg[s]["total_fsd"])

    def _seg_status_badge(rank: int, fsd: float, spend: float) -> str:
        if rank < 3:
            return '<span style="background:#422006;color:#f59e0b;border:1px solid #78350f;border-radius:20px;padding:2px 8px;font-size:.7rem;font-weight:600;white-space:nowrap;">&#127942; Top 3</span>'
        if fsd == 0 and spend < 50:
            return '<span style="background:#1a1f2e;color:#64748b;border:1px solid #2a3040;border-radius:20px;padding:2px 8px;font-size:.7rem;font-weight:600;white-space:nowrap;">&#9679; No Data</span>'
        if fsd == 0:
            return '<span style="background:#2a1a0a;color:#f59e0b;border:1px solid #78350f44;border-radius:20px;padding:2px 8px;font-size:.7rem;font-weight:600;white-space:nowrap;">&#9888; Paused</span>'
        if spend < 200:
            return '<span style="background:#0c1f2e;color:#38bdf8;border:1px solid #0369a144;border-radius:20px;padding:2px 8px;font-size:.7rem;font-weight:600;white-space:nowrap;">&#128300; Limited</span>'
        return '<span style="background:#0d1f0d;color:#34d399;border:1px solid #05603444;border-radius:20px;padding:2px 8px;font-size:.7rem;font-weight:600;white-space:nowrap;">&#10003; Active</span>'

    table_rows = ""
    for rank, seg in enumerate(sorted_segs):
        a     = seg_agg[seg]
        paid  = stripe_seg.get(seg, {}).get("paid", 0)
        color = SEGMENT_COLORS.get(seg, "#94a3b8")
        fsd   = a["total_fsd"]
        spend = a["total_spend"]
        impr  = a["total_impr"]
        clicks = a["total_link_clicks"]
        paid_rate = (paid / fsd * 100) if fsd > 0 else 0.0
        cp = cpac(spend, paid)
        badge = _seg_status_badge(rank, fsd, spend)
        dot = f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:{color};margin-right:6px;vertical-align:middle;"></span>'
        table_rows += f"""
        <tr>
          <td class="name-cell" style="color:{color}">{dot}{seg}</td>
          <td>${spend:,.2f}</td>
          <td>{impr:,}</td>
          <td>{clicks:,}</td>
          <td>{a['avg_ctr']:.2f}%</td>
          <td>${a['avg_cpc']:.3f}</td>
          <td>${a['avg_cpm']:.2f}</td>
          <td>{int(fsd)}</td>
          <td>{paid}</td>
          <td>{paid_rate:.1f}%</td>
          <td>{fmt_cpac(cp)}</td>
          <td style="text-align:center">{badge}</td>
        </tr>"""

    # ----------------------------------------------------------------
    # Section 2: Elimination timeline
    # ----------------------------------------------------------------
    elimination_rows = """
        <tr>
          <td>May 18</td>
          <td>AI-Curious Parent, Homeschool</td>
          <td>Paused by Tuan</td>
          <td>AI-Curious: 2 FSD in 6 days, $0.97 CPC (4x avg), CPM $52.56 — highest in cohort. Homeschool: 8 FSD, smallest addressable niche, insufficient volume signal.</td>
        </tr>
        <tr>
          <td>May 22</td>
          <td>iPad Battle Mom, Selective Mutism, Homework Meltdown</td>
          <td>Paused — budget reallocation</td>
          <td>All three showed stalling FSD after 10 days. iPad Battle Mom (17 FSD, $18.85 CPaC) and Homework Meltdown (20 FSD) were outpaced by the surviving 5. Selective Mutism had niche audience ceiling concerns.</td>
        </tr>
        <tr>
          <td>~May 27</td>
          <td>Anxiety Regulation, ADHD-EF Intervention</td>
          <td>Phased out — final 3 selected</td>
          <td>Anxiety Regulation: strong FSD (32) but lower paid conversion rate vs. top 3. ADHD-EF: only 21 FSD in 15 days — below threshold for scale. Top 3 (NBD, SP, RC) showed superior combined CTR, FSD volume, and paid depth.</td>
        </tr>"""

    # ----------------------------------------------------------------
    # Section 3: Weekly table for top 3 (segment totals per week)
    # ----------------------------------------------------------------
    weekly_rows = ""
    for seg_name in ["Nostalgia Bridge Dad", "Sturdy Parenting", "Routine-Chaos"]:
        color = SEGMENT_COLORS.get(seg_name, "#94a3b8")
        for week_label, spend, fsd, avg_ctr, avg_cpc in weekly_seg.get(seg_name, []):
            weekly_rows += f"""
        <tr>
          <td style="color:{color};font-weight:600">{seg_name}</td>
          <td>{week_label}</td>
          <td>${spend:,.2f}</td>
          <td>{int(fsd)}</td>
          <td>{avg_ctr:.2f}%</td>
          <td>${avg_cpc:.3f}</td>
        </tr>"""

    # ----------------------------------------------------------------
    # Section 4: Stripe deposits table
    # ----------------------------------------------------------------
    source_order = [
        ("6a-nostalgia-bridge",      "Nostalgia Bridge Dad"),
        ("2b-sturdy-parenting",      "Sturdy Parenting"),
        ("1d-routine-chaos",         "Routine-Chaos"),
        ("1c-anxiety-regulation",    "Anxiety Regulation"),
        ("5a-pcit-at-home",          "ADHD-EF Intervention"),
        ("1b-homework-meltdown",     "Homework Meltdown"),
        ("5b-pcit-sm-at-home",       "Selective Mutism"),
        ("1a-screen-time",           "iPad Battle Mom"),
        ("3a-first-ai-introduction", "AI-Curious Parent"),
        ("2c-homeschool",            "Homeschool"),
    ]
    stripe_rows = ""
    total_s_paid = total_s_pend = total_s_fsd_all = 0
    for source, cname in source_order:
        s = stripe[cname]
        paid = s["paid"]
        pending = s["pending"]
        fsd_for_camp = dict((c[0], c[7]) for c in CAMPAIGNS)[cname]
        paid_rate_s = pct(paid, fsd_for_camp)
        total_s_paid += paid
        total_s_pend += pending
        total_s_fsd_all += fsd_for_camp
        stripe_rows += f"""
        <tr>
          <td class="mono">{source}</td>
          <td>{cname}</td>
          <td>{fsd_for_camp}</td>
          <td>{s['total']}</td>
          <td class="paid-cell">{paid}</td>
          <td>{pending}</td>
          <td>{paid_rate_s:.1f}%</td>
        </tr>"""
    stripe_rows += f"""
        <tr class="total-row">
          <td colspan="2"><strong>TOTAL</strong></td>
          <td><strong>{total_s_fsd_all}</strong></td>
          <td><strong>{total_s_paid + total_s_pend}</strong></td>
          <td class="paid-cell"><strong>{total_s_paid}</strong></td>
          <td><strong>{total_s_pend}</strong></td>
          <td><strong>{pct(total_s_paid, total_s_fsd_all):.1f}%</strong></td>
        </tr>"""

    # ----------------------------------------------------------------
    # Section 5: Key decisions log
    # ----------------------------------------------------------------
    decisions_rows = """
        <tr>
          <td>May 13</td>
          <td>Launch all 10 campaigns</td>
          <td>All 10</td>
          <td>Equal budget testing begins. ~$50/day per campaign.</td>
        </tr>
        <tr>
          <td>May 18</td>
          <td>Pause 2 lowest performers</td>
          <td>AI-Curious Parent, Homeschool</td>
          <td>AI-Curious: 2 FSD / 6 days, CPC $0.97 — statistically weak. Homeschool: only 8 FSD, niche ceiling apparent. Both paused by Tuan to preserve budget.</td>
        </tr>
        <tr>
          <td>May 22</td>
          <td>Pause 3 mid-tier campaigns</td>
          <td>iPad Battle Mom, Selective Mutism, Homework Meltdown</td>
          <td>After 10 days, all three fell below FSD trajectory vs. surviving campaigns. Budget consolidated to top 5.</td>
        </tr>
        <tr>
          <td>~May 27</td>
          <td>Final selection — keep top 3</td>
          <td>Anxiety Regulation, ADHD-EF stopped</td>
          <td>Anxiety Regulation (32 FSD) and ADHD-EF (21 FSD) showed weaker paid conversion depth. Top 3 retained for scaling phase.</td>
        </tr>
        <tr>
          <td>May 28</td>
          <td>Campaign test window closes</td>
          <td>Nostalgia Bridge Dad, Sturdy Parenting, Routine-Chaos</td>
          <td>All three ran full 16-day window. Final data collected. Scaling decisions to follow.</td>
        </tr>"""

    # Build sections 6–13
    s6_html  = _build_retro_s6_customer_profiles(stripe_seg, seg_agg, weekly_seg)
    s7_html  = _build_retro_s7_ad_format_verdict(seg_ad_perf, extra)
    s8_html  = _build_retro_s8_device_breakdown(extra)
    s9_html  = _build_retro_s9_anomalies(seg_daily_cpr, extra)
    s10_html = _build_retro_s10_exclusion_plan(stripe)
    s11_html = _build_retro_s11_scaleup(stripe_seg)
    s12_html = _build_retro_s12_format_validation()
    s13_html = _build_retro_s13_placement_pc()

    # ================================================================
    # ASSEMBLE RETRO HTML
    # ================================================================
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Nowa Campaign Retrospective — May 2026</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #080b12; color: #e4e7ef; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Consolas', monospace; font-size: 14px; line-height: 1.5; }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 0 24px; }}

  /* HEADER */
  .retro-header {{ background: #0d1018; border-bottom: 2px solid #1e2535; padding: 40px 0 32px; }}
  .retro-title {{ font-size: 26px; font-weight: 700; color: #f8fafc; margin-bottom: 8px; }}
  .retro-subtitle {{ font-size: 14px; color: #64748b; }}
  .retro-badges {{ display: flex; gap: 12px; margin-top: 16px; flex-wrap: wrap; }}
  .retro-badge {{ padding: 4px 14px; border-radius: 20px; font-size: 12px; font-weight: 600; }}
  .badge-launched {{ background: #0c2a1f; color: #34d399; border: 1px solid #065f46; }}
  .badge-paused {{ background: #2a0f0f; color: #f87171; border: 1px solid #7f1d1d; }}
  .badge-survived {{ background: #422006; color: #f59e0b; border: 1px solid #78350f; }}

  /* SECTIONS */
  section {{ padding: 48px 0; border-bottom: 1px solid #0f172a; }}
  .section-title {{ font-size: 16px; font-weight: 700; color: #94a3b8; text-transform: uppercase; letter-spacing: 2px; margin-bottom: 24px; padding-bottom: 10px; border-bottom: 1px solid #1e2535; }}

  /* TABLES */
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  thead th {{ background: #0f172a; color: #64748b; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; padding: 10px 12px; text-align: left; border-bottom: 1px solid #1e2535; white-space: nowrap; }}
  tbody td {{ padding: 10px 12px; border-bottom: 1px solid #0f172a; vertical-align: middle; }}
  tbody tr:hover td {{ background: #0d1018; }}
  tbody tr:last-child td {{ border-bottom: none; }}
  .name-cell {{ font-weight: 600; color: #e4e7ef; min-width: 160px; }}
  .paid-cell {{ color: #34d399; font-weight: 700; }}
  .total-row td {{ background: #0f172a; font-weight: 700; border-top: 2px solid #1e2535; }}
  .mono {{ font-family: 'Consolas', 'Courier New', monospace; font-size: 12px; color: #64748b; }}
  .table-wrap {{ overflow-x: auto; border: 1px solid #1e2535; border-radius: 8px; }}

  /* VERDICT CHIPS */
  .v-winner {{ color: #34d399; font-weight: 700; }}
  .v-cut {{ color: #f87171; font-weight: 700; }}
  .v-inconclusive {{ color: #f59e0b; font-weight: 600; }}

  /* FORMAT CHIPS */
  .format-chip {{ padding: 3px 10px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
  .format-image {{ background: #1e3a5f; color: #60a5fa; }}
  .format-video {{ background: #2e1f5e; color: #a78bfa; }}
  .format-carousel {{ background: #3d2e0a; color: #f59e0b; }}

  /* SECTION 6: CUSTOMER PROFILES */
  .profile-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }}
  @media (max-width: 900px) {{ .profile-grid {{ grid-template-columns: 1fr; }} }}
  .profile-card {{ background: #0d1018; border: 1px solid #1e2535; border-radius: 10px; padding: 22px; }}
  .profile-header {{ font-size: 15px; font-weight: 700; margin-bottom: 10px; }}
  .profile-desc {{ font-size: 13px; color: #64748b; margin-bottom: 16px; line-height: 1.6; }}
  .profile-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 16px; }}
  .profile-item {{ background: #080b12; border: 1px solid #1e2535; border-radius: 6px; padding: 10px 12px; }}
  .profile-item-label {{ font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: #475569; margin-bottom: 4px; }}
  .profile-item-val {{ font-size: 15px; font-weight: 700; color: #e4e7ef; }}
  .profile-scale {{ padding: 12px 14px; border-radius: 8px; }}
  .profile-scale-badge {{ font-size: 14px; font-weight: 700; margin-bottom: 6px; display: flex; align-items: center; gap: 6px; }}
  .profile-scale-detail {{ font-size: 12px; color: #64748b; margin-bottom: 4px; }}
  .profile-scale-note {{ font-size: 12px; color: #94a3b8; font-style: italic; }}

  /* SECTION 8: DEVICE BREAKDOWN */
  .device-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }}
  @media (max-width: 900px) {{ .device-grid {{ grid-template-columns: 1fr; }} }}
  .device-block {{ background: #0d1018; border: 1px solid #1e2535; border-radius: 10px; padding: 20px; }}
  .device-block-title {{ font-size: 14px; font-weight: 700; margin-bottom: 4px; }}
  .device-verdict {{ font-size: 12px; color: #64748b; margin-top: 10px; }}

  /* SECTION 9: ANOMALIES */
  .anomaly-title {{ font-size: 14px; font-weight: 700; margin-bottom: 4px; }}

  /* SECTION 10: EXCLUSION PLAN */
  .excl-badge {{ padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: 700; white-space: nowrap; }}
  .excl-yes {{ background: #3d1010; color: #f87171; border: 1px solid #7f1d1d; }}
  .excl-nudge {{ background: #422006; color: #f59e0b; border: 1px solid #78350f; }}
  .excl-look {{ background: #1e3a5f; color: #60a5fa; border: 1px solid #1e40af; }}

  /* SECTION 11: SCALE-UP */
  .scale-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }}
  @media (max-width: 900px) {{ .scale-grid {{ grid-template-columns: 1fr; }} }}
  .scale-card {{ background: #0d1018; border: 1px solid #1e2535; border-radius: 10px; padding: 22px; }}
  .scale-header {{ display: flex; align-items: flex-start; gap: 12px; margin-bottom: 16px; }}
  .scale-icon {{ font-size: 24px; flex-shrink: 0; }}
  .scale-name {{ font-size: 14px; font-weight: 700; margin-bottom: 2px; }}
  .scale-headline {{ font-size: 13px; color: #94a3b8; }}
  .scale-confidence {{ margin-left: auto; font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 12px; white-space: nowrap; flex-shrink: 0; }}
  .scale-kpis {{ display: flex; gap: 12px; margin-bottom: 14px; }}
  .scale-kpi {{ flex: 1; background: #080b12; border: 1px solid #1e2535; border-radius: 6px; padding: 10px 12px; }}
  .scale-kpi-label {{ font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: #475569; margin-bottom: 4px; }}
  .scale-kpi-val {{ font-size: 18px; font-weight: 800; color: #e4e7ef; }}
  .scale-reason {{ font-size: 13px; color: #94a3b8; line-height: 1.7; margin-bottom: 14px; }}
  .scale-actions-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #475569; margin-bottom: 8px; }}
  .scale-actions {{ padding-left: 16px; }}

  /* MISC */
  .note {{ font-size: 12px; color: #475569; margin-top: 12px; font-style: italic; }}
  footer {{ padding: 32px 0; text-align: center; font-size: 12px; color: #334155; }}
</style>
</head>
<body>

<!-- HEADER -->
<div class="retro-header">
  <div class="container">
    <div class="retro-title">Nowa Campaign Retrospective &mdash; May 12 to May 31, 2026</div>
    <div class="retro-subtitle">Meta Ads audience testing across 10 parent segments &middot; Full data + elimination analysis</div>
    <div class="retro-badges">
      <span class="retro-badge badge-launched">10 Campaigns Launched</span>
      <span class="retro-badge badge-paused">7 Paused</span>
      <span class="retro-badge badge-survived">3 Survived to Scale</span>
    </div>
  </div>
</div>

<!-- SECTION 1: SEGMENT FULL METRICS TABLE -->
<section>
  <div class="container">
    <div class="section-title">All 10 Audience Segments &mdash; Full Metrics (sorted by FSD)</div>
    <p style="font-size:12px;color:#475569;margin-bottom:14px;font-style:italic;">
      Segment totals aggregated from adset-level data across all 13 campaigns (SALES + LEADS). May 11&ndash;31, 2026.
      Each ad set is attributed to its segment by name prefix regardless of which campaign it belongs to.
    </p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Segment</th>
            <th>Total Spend</th>
            <th>Impressions</th>
            <th>Link Clicks</th>
            <th>CTR%</th>
            <th>CPC$</th>
            <th>CPM$</th>
            <th>FSD</th>
            <th>Paid</th>
            <th>Paid Rate%</th>
            <th>CPaC$</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {table_rows}
        </tbody>
      </table>
    </div>
    <div class="note">FSD = form_submit_deposit (Meta lead form). Paid Rate = Paid Deposits / FSD. CPaC = Spend / Paid Deposits. LEADS campaign ad sets are included in their respective segment totals.</div>
  </div>
</section>

<!-- SECTION 2: ELIMINATION TIMELINE -->
<section>
  <div class="container">
    <div class="section-title">Elimination Timeline</div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Campaigns Paused</th>
            <th>Action</th>
            <th>Reason / Metrics at Time of Pause</th>
          </tr>
        </thead>
        <tbody>
          {elimination_rows}
        </tbody>
      </table>
    </div>
  </div>
</section>

<!-- SECTION 3: WEEKLY PERFORMANCE -->
<section>
  <div class="container">
    <div class="section-title">Weekly Performance Trend &mdash; Top 3 Segments</div>
    <p style="font-size:12px;color:#475569;margin-bottom:14px;font-style:italic;">
      Segment totals per week — all ad sets belonging to each segment summed across ALL campaigns (SALES + LEADS).
    </p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Segment</th>
            <th>Week</th>
            <th>Spend</th>
            <th>FSD</th>
            <th>Avg CTR</th>
            <th>Avg CPC</th>
          </tr>
        </thead>
        <tbody>
          {weekly_rows}
        </tbody>
      </table>
    </div>
    <div class="note">W1: May 13&ndash;19 &middot; W2: May 20&ndash;26 &middot; W3: May 27&ndash;31. Spend and FSD are sums across all ad sets in the segment. CTR and CPC derived from segment totals.</div>
  </div>
</section>

<!-- SECTION 4: STRIPE DEPOSITS -->
<section>
  <div class="container">
    <div class="section-title">Stripe Deposits by Segment</div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Source</th>
            <th>Campaign</th>
            <th>Total FSD (Meta)</th>
            <th>Total Sign-ups (Stripe)</th>
            <th>Paid</th>
            <th>Pending</th>
            <th>Paid Rate (Paid/FSD)</th>
          </tr>
        </thead>
        <tbody>
          {stripe_rows}
        </tbody>
      </table>
    </div>
    <div class="note">Paid Rate = Paid Deposits / Meta FSD. Stripe sign-ups may differ from FSD due to timing and cross-session attribution.</div>
  </div>
</section>

<!-- SECTION 5: DECISIONS LOG -->
<section>
  <div class="container">
    <div class="section-title">Key Decisions Log</div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Action</th>
            <th>Campaigns Affected</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {decisions_rows}
        </tbody>
      </table>
    </div>
  </div>
</section>

<!-- SECTION 6: CUSTOMER PROFILES -->
{s6_html}

<!-- SECTION 7: AD FORMAT & ANGLE VERDICT -->
{s7_html}

<!-- SECTION 8: DESKTOP VS MOBILE -->
{s8_html}

<!-- SECTION 9: TIME SERIES ANOMALIES -->
{s9_html}

<!-- SECTION 10: AUDIENCE EXCLUSION PLAN -->
{s10_html}

<!-- SECTION 11: SCALE-UP RECOMMENDATIONS -->
{s11_html}

<!-- SECTION 12: FORMAT VALIDATION — STATIC VS VIDEO -->
{s12_html}

<!-- SECTION 13: PLACEMENT — PC VS MOBILE -->
{s13_html}

<footer>
  <div class="container">
    Nowa &nbsp;&middot;&nbsp; Internal Retrospective &nbsp;&middot;&nbsp; May 2026 &nbsp;&middot;&nbsp; Data: Meta Ads API + Stripe
  </div>
</footer>
</body>
</html>"""

    out = REPORTS_DIR / "retro_report.html"
    out.write_text(html, encoding="utf-8")
    print(f"Written: {out}")
    return str(out)


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    from src.config import load_settings
    settings = load_settings()

    # ---- Investor report data (campaign-level, unchanged) ----
    print("Loading Stripe data (campaign-keyed) ...")
    stripe = load_stripe_data()
    print("Stripe paid deposits by campaign:")
    for name, data in sorted(stripe.items(), key=lambda x: -x[1]["paid"]):
        print(f"  {name}: paid={data['paid']}, pending={data['pending']}")

    print("\nLoading weekly data from DB (for investor CAC trend) ...")
    weekly = load_weekly_data()

    print("\nLoading extra data (device/ad/CPR) ...")
    extra = load_extra_data()

    print("\nBuilding investor report ...")
    inv = build_investor_report(stripe, extra)

    # ---- Retro report data (segment-level, new) ----
    print("\n--- Segment data for retro report ---")

    print("\nLoading Stripe by segment ...")
    stripe_seg = load_stripe_by_segment()
    print("Stripe paid deposits by segment:")
    for seg, data in sorted(stripe_seg.items(), key=lambda x: -x[1]["paid"]):
        if data["paid"] > 0 or data["pending"] > 0:
            print(f"  {seg}: paid={data['paid']}, pending={data['pending']}")

    print("\nLoading segment totals from Meta API (May 11–31) ...")
    seg_agg = load_segment_data(settings)

    print("\nLoading weekly segment data from Meta API ...")
    weekly_seg = load_weekly_segment_data(settings)

    print("\nLoading daily segment CPR from Meta API ...")
    seg_daily_cpr = load_segment_daily_cpr(settings)

    print("\nLoading segment ad performance from Meta API ...")
    seg_ad_perf = load_segment_ad_performance(settings)
    print(f"  {len(seg_ad_perf)} ad-level rows fetched")

    print("\nBuilding retro report ...")
    retro = build_retro_report(stripe_seg, weekly_seg, extra, seg_agg, seg_daily_cpr, seg_ad_perf)

    print("\nDone.")
    print(f"  Investor: {inv}")
    print(f"  Retro:    {retro}")
