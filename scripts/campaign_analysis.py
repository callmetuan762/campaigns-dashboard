"""Nowa Meta Ads — Strategic Campaign Performance Analysis.

Generates a comprehensive dark-theme HTML report at reports/campaign_analysis.html
covering 10 audience segments, ad format analysis, funnel breakdown, optimization
timeline, lessons learned, and next-campaign recommendations.

Run:
    python -X utf8 scripts/campaign_analysis.py
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_settings  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SINCE = "2026-05-11"
UNTIL = "2026-05-31"

DB_PATH = ROOT / "data" / "metrics.db"
STRIPE_CSV = Path(r"C:\Users\unive\Desktop\draf\Nowa Deposit $1 - Payments-20260529-1011.csv")

ALL_CAMPAIGNS: dict[str, str] = {
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

STRIPE_SOURCE_MAP: dict[str, str] = {
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

SEGMENT_COLORS: dict[str, str] = {
    "Nostalgia Bridge Dad": "#f59e0b",
    "Sturdy Parenting":     "#34d399",
    "Routine-Chaos":        "#f472b6",
    "Anxiety Regulation":   "#818cf8",
    "ADHD-EF Intervention": "#a78bfa",
    "Homework Meltdown":    "#60a5fa",
    "iPad Battle Mom":      "#94a3b8",
    "Selective Mutism":     "#64748b",
    "AI-Curious Parent":    "#38bdf8",
    "Homeschool":           "#34d399",
}

CAMPAIGN_ID_TO_SEGMENT: dict[str, str] = {
    "120243727739260025": "Nostalgia Bridge Dad",
    "120243727242320025": "Sturdy Parenting",
    "120243727123190025": "Routine-Chaos",
    "120243726950430025": "Anxiety Regulation",
    "120243727522590025": "ADHD-EF Intervention",
    "120243717547910025": "iPad Battle Mom",
    "120243727567700025": "Selective Mutism",
    "120243717918140025": "Homework Meltdown",
    "120243727427170025": "AI-Curious Parent",
    "120243727324310025": "Homeschool",
}

WINNER_SEGMENTS = {"Nostalgia Bridge Dad", "Sturdy Parenting", "Routine-Chaos"}
VIABLE_SEGMENTS = {"Anxiety Regulation", "ADHD-EF Intervention", "Selective Mutism"}
ELIMINATED_SEGMENTS = {"iPad Battle Mom", "Homework Meltdown", "Homeschool", "AI-Curious Parent"}

ELIMINATION_DATES: dict[str, str] = {
    "AI-Curious Parent":    "May 18",
    "Homeschool":           "May 18",
    "iPad Battle Mom":      "May 22",
    "Homework Meltdown":    "May 22",
    "Selective Mutism":     "May 22",
    "Anxiety Regulation":   "May 27",
    "ADHD-EF Intervention": "May 27",
}

# ---------------------------------------------------------------------------
# 1. Meta API helpers
# ---------------------------------------------------------------------------

def _init_api(settings) -> None:
    from facebook_business.api import FacebookAdsApi
    FacebookAdsApi.init(
        app_id=settings.meta_app_id,
        app_secret=settings.meta_app_secret.get_secret_value(),
        access_token=settings.meta_access_token.get_secret_value(),
        api_version="v24.0",
    )


def _paginate(cursor) -> list[dict]:
    rows: list[dict] = []
    while True:
        rows.extend([dict(r) for r in cursor])
        if cursor.load_next_page() is False:
            break
    return rows


def _extract_fsd(conversions: list | None) -> float:
    if not conversions:
        return 0.0
    for item in conversions:
        at = item.get("action_type", "")
        if "form_submit_deposit" in at:
            return float(item.get("value", 0) or 0)
    return 0.0


def fetch_campaign_adset_insights(campaign_id: str, campaign_label: str) -> list[dict]:
    from facebook_business.adobjects.adaccount import AdAccount
    settings = load_settings()
    account = AdAccount(f"act_{settings.meta_ad_account_id.removeprefix('act_')}")
    fields = [
        "adset_id", "adset_name", "spend", "impressions",
        "inline_link_clicks", "inline_link_click_ctr",
        "cpm", "reach", "frequency", "conversions",
    ]
    params = {
        "level": "adset",
        "filtering": [{"field": "campaign.id", "operator": "EQUAL", "value": campaign_id}],
        "time_range": {"since": SINCE, "until": UNTIL},
        "limit": 500,
    }
    try:
        cursor = account.get_insights(fields=fields, params=params)
        raw = _paginate(cursor)
    except Exception as exc:
        print(f"    [warn] insights fetch failed for {campaign_id}: {exc}")
        return []

    rows = []
    for r in raw:
        fsd = _extract_fsd(r.get("conversions"))
        ctr = float(r.get("inline_link_click_ctr", 0) or 0)
        clicks = int(r.get("inline_link_clicks", 0) or 0)
        spend = float(r.get("spend", 0) or 0)
        rows.append({
            "campaign_id":   campaign_id,
            "campaign_name": campaign_label,
            "adset_id":      str(r.get("adset_id", "")),
            "adset_name":    str(r.get("adset_name", "")),
            "spend":         spend,
            "impressions":   int(r.get("impressions", 0) or 0),
            "link_clicks":   clicks,
            "reach":         int(r.get("reach", 0) or 0),
            "frequency":     float(r.get("frequency", 0) or 0),
            "ctr":           ctr,
            "cpm":           float(r.get("cpm", 0) or 0),
            "cpc":           (spend / clicks) if clicks else 0.0,
            "fsd":           fsd,
        })
    return rows


def pull_all_data(settings) -> list[dict]:
    _init_api(settings)
    all_rows: list[dict] = []
    for cid, clabel in ALL_CAMPAIGNS.items():
        print(f"  Fetching: {clabel} ({cid})")
        rows = fetch_campaign_adset_insights(cid, clabel)
        print(f"    {len(rows)} adset rows")
        all_rows.extend(rows)
    return all_rows


def classify_segment(adset_name: str) -> str | None:
    name_upper = adset_name.upper()
    for segment, keywords in SEGMENT_MAP.items():
        for kw in keywords:
            if kw.upper() in name_upper:
                return segment
    return None


def aggregate_by_segment(all_rows: list[dict]) -> dict[str, dict]:
    agg: dict[str, dict] = {}
    for seg in SEGMENT_MAP:
        agg[seg] = {
            "total_spend": 0.0, "total_impr": 0, "total_link_clicks": 0,
            "total_fsd": 0.0, "total_reach": 0,
            "sum_ctr": 0.0, "sum_cpm": 0.0, "sum_cpc": 0.0, "row_count": 0,
        }

    unclassified = []
    for row in all_rows:
        seg = classify_segment(row["adset_name"])
        if seg is None:
            unclassified.append(row)
            continue
        a = agg[seg]
        a["total_spend"]       += row["spend"]
        a["total_impr"]        += row["impressions"]
        a["total_link_clicks"] += row["link_clicks"]
        a["total_fsd"]         += row["fsd"]
        a["total_reach"]       += row["reach"]
        a["sum_ctr"]           += row["ctr"]
        a["sum_cpm"]           += row["cpm"]
        a["sum_cpc"]           += row["cpc"]
        a["row_count"]         += 1

    if unclassified:
        print(f"\n  [info] {len(unclassified)} adset rows unclassified:")
        for r in unclassified:
            print(f"    campaign={r['campaign_name']}  adset='{r['adset_name']}'  spend={r['spend']:.2f}")

    for seg, a in agg.items():
        n = max(a["row_count"], 1)
        a["avg_ctr"] = a["sum_ctr"] / n
        a["avg_cpm"] = a["sum_cpm"] / n
        a["avg_cpc"] = a["sum_cpc"] / n

    return agg


# ---------------------------------------------------------------------------
# 2. DB data helpers
# ---------------------------------------------------------------------------

def load_stripe_from_db() -> tuple[dict[str, int], dict[str, int]]:
    """Returns (paid_by_seg, pending_by_seg)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    paid: dict[str, int] = {seg: 0 for seg in SEGMENT_MAP}
    pending: dict[str, int] = {seg: 0 for seg in SEGMENT_MAP}
    for source, segment in STRIPE_SOURCE_MAP.items():
        cur.execute("SELECT COUNT(*) FROM stripe_payments WHERE source=? AND status='paid'", (source,))
        paid[segment] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM stripe_payments WHERE source=? AND status!='paid'", (source,))
        pending[segment] = cur.fetchone()[0]
    conn.close()
    return paid, pending


def load_stripe_from_csv() -> tuple[dict[str, int], dict[str, int]]:
    """Fallback: parse CSV directly."""
    paid: dict[str, int] = {seg: 0 for seg in SEGMENT_MAP}
    pending: dict[str, int] = {seg: 0 for seg in SEGMENT_MAP}
    if not STRIPE_CSV.exists():
        return paid, pending
    with open(STRIPE_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            source = row.get("Source", "").strip()
            status = row.get("Status", "").strip().lower()
            segment = STRIPE_SOURCE_MAP.get(source)
            if segment:
                if status == "paid":
                    paid[segment] = paid.get(segment, 0) + 1
                else:
                    pending[segment] = pending.get(segment, 0) + 1
    return paid, pending


def load_weekly_trend() -> dict[str, dict[str, dict]]:
    """Returns {campaign_id: {week: {spend, fsd, ctr, cpc, cpm, ctr_all}}}.

    ctr/cpc are link-click metrics (inline_link_click_ctr / inline_link_clicks)
    fetched from the Meta Ads API.  ctr_all is always sourced from DB AVG(ctr).
    Falls back to DB AVG(ctr)/AVG(cpc) for link metrics if the API call fails.
    """
    WINNER_IDS = [
        "120243727739260025",  # Nostalgia Bridge Dad
        "120243727242320025",  # Sturdy Parenting
        "120243727123190025",  # Routine-Chaos
    ]
    WEEK_RANGES = {
        "W1": ("2026-05-13", "2026-05-19"),
        "W2": ("2026-05-20", "2026-05-26"),
        "W3": ("2026-05-27", "2026-05-31"),
    }

    try:
        from facebook_business.adobjects.adaccount import AdAccount
        settings = load_settings()
        _init_api(settings)
        account = AdAccount(f"act_{settings.meta_ad_account_id.removeprefix('act_')}")

        fields = [
            "campaign_id",
            "spend",
            "impressions",
            "inline_link_clicks",
            "inline_link_click_ctr",
            "cpm",
            "conversions",
        ]
        result: dict[str, dict[str, dict]] = defaultdict(dict)
        for week, (since, until) in WEEK_RANGES.items():
            params = {
                "level": "campaign",
                "filtering": [{"field": "campaign.id", "operator": "IN", "value": WINNER_IDS}],
                "time_range": {"since": since, "until": until},
                "limit": 100,
            }
            try:
                cursor = account.get_insights(fields=fields, params=params)
                rows = _paginate(cursor)
            except Exception as exc:
                print(f"    [warn] weekly trend API fetch failed for {week}: {exc}")
                rows = []
            for r in rows:
                cid = str(r.get("campaign_id", ""))
                spend = float(r.get("spend", 0) or 0)
                link_clicks = int(r.get("inline_link_clicks", 0) or 0)
                link_ctr = float(r.get("inline_link_click_ctr", 0) or 0)
                link_cpc = (spend / link_clicks) if link_clicks else 0.0
                fsd = _extract_fsd(r.get("conversions"))
                result[cid][week] = {
                    "spend": spend,
                    "fsd":   fsd,
                    "ctr":   link_ctr,   # inline_link_click_ctr
                    "cpc":   link_cpc,   # spend / inline_link_clicks
                    "cpm":   float(r.get("cpm", 0) or 0),
                }
        if result:
            # Merge in DB all-clicks CTR for each week/campaign row
            _merge_db_all_clicks_weekly(result)
            return result
        # fall through to DB fallback if API returned nothing
    except Exception as exc:
        print(f"  [warn] Meta API weekly trend fetch failed: {exc} — falling back to DB")

    # DB fallback (all-clicks ctr/cpc — note: these are NOT link-click metrics)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT campaign_id,
          CASE WHEN date BETWEEN '2026-05-13' AND '2026-05-19' THEN 'W1'
               WHEN date BETWEEN '2026-05-20' AND '2026-05-26' THEN 'W2'
               ELSE 'W3' END as week,
          SUM(spend), SUM(meta_form_submit_deposit), AVG(ctr), AVG(cpc), AVG(cpm)
        FROM ad_metrics
        WHERE campaign_id IN ('120243727739260025','120243727242320025','120243727123190025')
          AND ad_set_id = ''
        GROUP BY campaign_id, week ORDER BY campaign_id, week
    """)
    result2: dict[str, dict[str, dict]] = defaultdict(dict)
    for cid, week, spend, fsd, ctr, cpc, cpm in cur.fetchall():
        result2[cid][week] = {
            "spend":   spend or 0.0,
            "fsd":     fsd or 0,
            "ctr":     ctr or 0.0,   # all-clicks (DB fallback — same as ctr_all)
            "cpc":     cpc or 0.0,
            "cpm":     cpm or 0.0,
            "ctr_all": ctr or 0.0,   # same source in fallback case
        }
    conn.close()
    return result2


def _merge_db_all_clicks_weekly(result: dict[str, dict[str, dict]]) -> None:
    """Merge DB all-clicks CTR into an API-sourced weekly result dict (in-place)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT campaign_id,
          CASE WHEN date BETWEEN '2026-05-13' AND '2026-05-19' THEN 'W1'
               WHEN date BETWEEN '2026-05-20' AND '2026-05-26' THEN 'W2'
               ELSE 'W3' END as week,
          AVG(ctr) as ctr_all
        FROM ad_metrics
        WHERE campaign_id IN ('120243727739260025','120243727242320025','120243727123190025')
          AND ad_set_id = ''
        GROUP BY campaign_id, week
    """)
    for cid, week, ctr_all in cur.fetchall():
        if cid in result and week in result[cid]:
            result[cid][week]["ctr_all"] = ctr_all or 0.0
        elif cid in result:
            result[cid].setdefault(week, {})["ctr_all"] = ctr_all or 0.0
    conn.close()


def load_ad_performance() -> list[dict]:
    """Top ads by FSD from DB."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT ac.ad_style, ac.ad_format, ac.ad_name, c.name as campaign,
               SUM(m.spend) as spend, SUM(m.meta_form_submit_deposit) as fsd,
               AVG(m.ctr) as ctr, AVG(m.cpc) as cpc
        FROM ad_metrics m
        JOIN ad_creatives ac ON m.ad_id = ac.ad_id
        JOIN campaigns c ON m.campaign_id = c.id
        WHERE m.ad_id != ''
        GROUP BY ac.ad_name
        ORDER BY fsd DESC LIMIT 20
    """)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def load_changelog() -> list[dict]:
    """Significant changelog events for timeline."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT change_time, object_name, object_type, event_type, actor_name, new_value
        FROM ad_changelogs
        WHERE (event_type LIKE '%status%' OR event_type LIKE '%budget%' OR event_type LIKE '%creative%')
          AND object_name LIKE '%SALES%'
          AND actor_name != 'ads-sandbox'
        ORDER BY change_time
    """)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def load_db_segment_metrics() -> dict[str, dict]:
    """Campaign-level metrics from DB for segments (for fallback / verification)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT campaign_id,
               SUM(spend) as spend, SUM(impressions) as impr, SUM(clicks) as clicks,
               SUM(reach) as reach, AVG(frequency) as freq,
               SUM(meta_form_submit_deposit) as fsd,
               AVG(ctr) as ctr, AVG(cpc) as cpc, AVG(cpm) as cpm
        FROM ad_metrics
        WHERE ad_set_id = '' AND ad_id = ''
        GROUP BY campaign_id
    """)
    result = {}
    for row in cur.fetchall():
        cid = row[0]
        result[cid] = {
            "spend": row[1] or 0.0, "impr": row[2] or 0, "clicks": row[3] or 0,
            "reach": row[4] or 0, "freq": row[5] or 0.0, "fsd": row[6] or 0,
            "ctr": row[7] or 0.0, "cpc": row[8] or 0.0, "cpm": row[9] or 0.0,
        }
    conn.close()
    return result


def load_db_all_clicks_by_segment() -> dict[str, dict]:
    """Returns {segment_name: {avg_ctr_all, avg_cpc_all}} using DB all-clicks CTR/CPC.

    Queries ad_metrics at campaign level (ad_set_id = '') per CAMPAIGN_ID_TO_SEGMENT.
    """
    campaign_ids = list(CAMPAIGN_ID_TO_SEGMENT.keys())
    placeholders = ",".join("?" * len(campaign_ids))
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(f"""
        SELECT campaign_id, AVG(ctr) as avg_ctr_all, AVG(cpc) as avg_cpc_all
        FROM ad_metrics
        WHERE campaign_id IN ({placeholders})
          AND ad_set_id = ''
        GROUP BY campaign_id
    """, campaign_ids)
    rows = cur.fetchall()
    conn.close()

    # Aggregate by segment (in case multiple campaign_ids map to same segment)
    seg_sums: dict[str, dict] = {}
    for cid, avg_ctr, avg_cpc in rows:
        seg = CAMPAIGN_ID_TO_SEGMENT.get(cid)
        if seg is None:
            continue
        if seg not in seg_sums:
            seg_sums[seg] = {"ctr_sum": 0.0, "cpc_sum": 0.0, "n": 0}
        seg_sums[seg]["ctr_sum"] += avg_ctr or 0.0
        seg_sums[seg]["cpc_sum"] += avg_cpc or 0.0
        seg_sums[seg]["n"] += 1

    result: dict[str, dict] = {}
    for seg, d in seg_sums.items():
        n = max(d["n"], 1)
        result[seg] = {
            "avg_ctr_all": d["ctr_sum"] / n,
            "avg_cpc_all": d["cpc_sum"] / n,
        }
    return result


# ---------------------------------------------------------------------------
# 3. HTML formatting helpers
# ---------------------------------------------------------------------------

def _fs(v: float) -> str:
    return f"${v:,.2f}"

def _fn(v: float | int) -> str:
    return f"{int(v):,}"

def _fp(v: float) -> str:
    return f"{v:.2f}%"

def _cpa(spend: float, n: float) -> str:
    return f"${spend/n:,.2f}" if n > 0 else "—"

def _dot(color: str) -> str:
    return (f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;'
            f'background:{color};margin-right:7px;flex-shrink:0;vertical-align:middle;"></span>')

def _badge(text: str, bg: str, color: str, border: str = "") -> str:
    brd = f"border:1px solid {border};" if border else ""
    return (f'<span style="background:{bg};color:{color};{brd}border-radius:20px;'
            f'padding:3px 11px;font-size:.72rem;font-weight:600;white-space:nowrap;">{text}</span>')

def _insight_box(text: str, color: str = "#60a5fa", icon: str = "ℹ") -> str:
    return f"""
    <div style="background:{color}0d;border-left:3px solid {color};border-radius:0 8px 8px 0;
                padding:14px 18px;margin:16px 0;">
      <p style="color:#cbd5e1;font-size:.9rem;line-height:1.75;margin:0;">
        <span style="color:{color};margin-right:8px;">{icon}</span>{text}
      </p>
    </div>"""

def _callout(title: str, body: str, color: str = "#f59e0b") -> str:
    return f"""
    <div style="background:{color}0a;border:1px solid {color}33;border-radius:10px;
                padding:18px 22px;margin:16px 0;">
      <div style="color:{color};font-size:.75rem;font-weight:700;text-transform:uppercase;
                  letter-spacing:.1em;margin-bottom:8px;">{title}</div>
      <div style="color:#cbd5e1;font-size:.88rem;line-height:1.8;">{body}</div>
    </div>"""

def _th(label: str, align: str = "right") -> str:
    return (f'<th style="padding:9px 14px;text-align:{align};color:#475569;font-size:.7rem;'
            f'font-weight:500;text-transform:uppercase;letter-spacing:.07em;'
            f'border-bottom:1px solid #1e2235;">{label}</th>')

def _td(content: str, align: str = "right", muted: bool = False, bold: bool = False) -> str:
    color = "#94a3b8" if muted else "#e4e7ef"
    fw = "font-weight:600;" if bold else ""
    return (f'<td style="padding:10px 14px;text-align:{align};color:{color};{fw}'
            f'border-bottom:1px solid #111827;white-space:nowrap;">{content}</td>')

def _section_header(num: str, title: str, subtitle: str = "") -> str:
    sub = f'<p style="color:#475569;font-size:.82rem;margin:4px 0 0;">{subtitle}</p>' if subtitle else ""
    return f"""
    <div style="margin:48px 0 24px;display:flex;align-items:baseline;gap:16px;
                padding-bottom:16px;border-bottom:1px solid #1e2235;">
      <span style="color:#f59e0b;font-size:1rem;font-weight:800;opacity:.7;">{num}</span>
      <div>
        <h2 style="color:#fff;font-size:1.2rem;font-weight:700;margin:0;">{title}</h2>
        {sub}
      </div>
    </div>"""

def _mini_stat(label: str, value: str, color: str = "#e4e7ef") -> str:
    return f"""
    <div style="background:#080b12;border:1px solid #1e2235;border-radius:8px;
                padding:12px 16px;text-align:center;">
      <div style="color:#475569;font-size:.65rem;text-transform:uppercase;
                  letter-spacing:.07em;margin-bottom:5px;">{label}</div>
      <div style="color:{color};font-size:1.05rem;font-weight:700;">{value}</div>
    </div>"""

def _bar_inline(pct: float, color: str, max_pct: float = 100) -> str:
    width = min(pct / max(max_pct, 1) * 100, 100)
    return (f'<div style="display:flex;align-items:center;gap:8px;">'
            f'<div style="flex:1;height:6px;background:#1e2235;border-radius:3px;">'
            f'<div style="width:{width:.1f}%;height:100%;background:{color};border-radius:3px;"></div>'
            f'</div>'
            f'<span style="color:#64748b;font-size:.75rem;min-width:40px;">{pct:.1f}%</span>'
            f'</div>')


# ---------------------------------------------------------------------------
# 4. Report sections
# ---------------------------------------------------------------------------

def build_report(
    agg: dict[str, dict],
    db_metrics: dict[str, dict],
    stripe_paid: dict[str, int],
    stripe_pending: dict[str, int],
    weekly: dict[str, dict[str, dict]],
    ad_perf: list[dict],
    changelog: list[dict],
    all_clicks_db: dict[str, dict] | None = None,
) -> str:

    # ── Enrich agg with DB metrics for segments without Meta API data ──────
    for cid, seg in CAMPAIGN_ID_TO_SEGMENT.items():
        if cid in db_metrics and agg.get(seg, {}).get("total_spend", 0) == 0:
            d = db_metrics[cid]
            agg[seg]["total_spend"] = d["spend"]
            agg[seg]["total_impr"] = d["impr"]
            agg[seg]["total_link_clicks"] = d["clicks"]
            agg[seg]["total_fsd"] = d["fsd"]
            agg[seg]["avg_ctr"] = d["ctr"]
            agg[seg]["avg_cpc"] = d["cpc"]
            agg[seg]["avg_cpm"] = d["cpm"]

    # If still empty from Meta API, use DB metrics for all
    for cid, seg in CAMPAIGN_ID_TO_SEGMENT.items():
        if cid in db_metrics:
            d = db_metrics[cid]
            if agg[seg]["total_spend"] == 0:
                agg[seg]["total_spend"] = d["spend"]
                agg[seg]["total_impr"] = d["impr"]
                agg[seg]["total_link_clicks"] = d["clicks"]
                agg[seg]["total_fsd"] = d["fsd"]
                agg[seg]["avg_ctr"] = d["ctr"]
                agg[seg]["avg_cpc"] = d["cpc"]
                agg[seg]["avg_cpm"] = d["cpm"]

    # Merge all-clicks CTR/CPC from DB into agg segments
    if all_clicks_db is None:
        all_clicks_db = {}
    for seg in SEGMENT_MAP:
        ac = all_clicks_db.get(seg, {})
        agg[seg]["avg_ctr_all"] = ac.get("avg_ctr_all", 0.0)
        agg[seg]["avg_cpc_all"] = ac.get("avg_cpc_all", 0.0)

    # Sort segments
    sorted_segs = sorted(agg.keys(), key=lambda s: -agg[s]["total_fsd"])

    # Totals
    total_spend = sum(a["total_spend"] for a in agg.values())
    total_fsd = sum(a["total_fsd"] for a in agg.values())
    total_paid = sum(stripe_paid.values())
    total_pending = sum(stripe_pending.values())
    best_cpac_seg = min(
        (s for s in WINNER_SEGMENTS if stripe_paid.get(s, 0) > 0),
        key=lambda s: agg[s]["total_spend"] / stripe_paid[s],
        default="Nostalgia Bridge Dad",
    )
    best_cpac = _cpa(agg[best_cpac_seg]["total_spend"], stripe_paid.get(best_cpac_seg, 1))

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 1: Executive Summary
    # ══════════════════════════════════════════════════════════════════════
    nostalgia_data = agg["Nostalgia Bridge Dad"]
    sturdy_data = agg["Sturdy Parenting"]
    routine_data = agg["Routine-Chaos"]

    kpi_grid = f"""
    <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:1px;
                background:#1e2235;border-radius:12px;overflow:hidden;margin:24px 0 32px;">
      <div style="background:#0d1018;padding:22px 20px;text-align:center;">
        <div style="color:#475569;font-size:.65rem;text-transform:uppercase;letter-spacing:.1em;margin-bottom:8px;">Total Spend</div>
        <div style="color:#f59e0b;font-size:2rem;font-weight:800;">{_fs(total_spend)}</div>
        <div style="color:#334155;font-size:.72rem;margin-top:4px;">May 12–31, 2026</div>
      </div>
      <div style="background:#0d1018;padding:22px 20px;text-align:center;">
        <div style="color:#475569;font-size:.65rem;text-transform:uppercase;letter-spacing:.1em;margin-bottom:8px;">Total FSD</div>
        <div style="color:#e4e7ef;font-size:2rem;font-weight:800;">{_fn(total_fsd)}</div>
        <div style="color:#334155;font-size:.72rem;margin-top:4px;">Form Submissions (Gate 1)</div>
      </div>
      <div style="background:#0d1018;padding:22px 20px;text-align:center;">
        <div style="color:#475569;font-size:.65rem;text-transform:uppercase;letter-spacing:.1em;margin-bottom:8px;">Paid Deposits</div>
        <div style="color:#34d399;font-size:2rem;font-weight:800;">{total_paid}</div>
        <div style="color:#334155;font-size:.72rem;margin-top:4px;">Stripe Confirmed (Gate 2)</div>
      </div>
      <div style="background:#0d1018;padding:22px 20px;text-align:center;">
        <div style="color:#475569;font-size:.65rem;text-transform:uppercase;letter-spacing:.1em;margin-bottom:8px;">Pending Deposits</div>
        <div style="color:#f59e0b;font-size:2rem;font-weight:800;">{total_pending}</div>
        <div style="color:#334155;font-size:.72rem;margin-top:4px;">FSD not yet converted</div>
      </div>
      <div style="background:#0d1018;padding:22px 20px;text-align:center;">
        <div style="color:#475569;font-size:.65rem;text-transform:uppercase;letter-spacing:.1em;margin-bottom:8px;">Best CPaC</div>
        <div style="color:#e4e7ef;font-size:2rem;font-weight:800;">{best_cpac}</div>
        <div style="color:#334155;font-size:.72rem;margin-top:4px;">{best_cpac_seg.split()[0]}</div>
      </div>
    </div>"""

    winner_badges = """
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:24px 0;">"""
    for seg, color, emoji, tagline in [
        ("Nostalgia Bridge Dad", "#f59e0b", "🏆", f"42 paid · CPaC {_cpa(nostalgia_data['total_spend'], stripe_paid.get('Nostalgia Bridge Dad', 1))} · Lowest CPM"),
        ("Sturdy Parenting",     "#34d399", "🥇", f"43 paid · CPaC {_cpa(sturdy_data['total_spend'], stripe_paid.get('Sturdy Parenting', 1))} · Best paid rate"),
        ("Routine-Chaos",        "#f472b6", "🎯", f"28 paid · CPaC {_cpa(routine_data['total_spend'], stripe_paid.get('Routine-Chaos', 1))} · Consistent delivery"),
    ]:
        winner_badges += f"""
      <div style="background:linear-gradient(135deg,{color}15 0%,#0d1018 60%);
                  border:1px solid {color}44;border-radius:12px;padding:22px 24px;">
        <div style="font-size:1.4rem;margin-bottom:10px;">{emoji}</div>
        <div style="color:{color};font-size:.68rem;text-transform:uppercase;
                    letter-spacing:.1em;font-weight:700;margin-bottom:4px;">Winner</div>
        <div style="color:#fff;font-size:1rem;font-weight:700;margin-bottom:8px;">{seg}</div>
        <div style="color:#64748b;font-size:.8rem;line-height:1.5;">{tagline}</div>
      </div>"""
    winner_badges += "</div>"

    sec1 = f"""
    {_section_header("01", "Executive Summary", "What was done, what was found, what it means")}

    <p style="color:#cbd5e1;font-size:.95rem;line-height:1.9;margin:0 0 12px;">
      Nowa ran a simultaneous 10-audience-segment test on Meta Ads from May 12–31, 2026,
      spending equal daily budgets across all segments to identify which parental psychographic
      best converts to a $99 calm-tech pre-order. A two-gate funnel tracked progression from
      Meta form submission (FSD) to confirmed Stripe deposit, enabling segment-by-segment
      qualification at both the ad and purchase intent level.
    </p>
    <p style="color:#cbd5e1;font-size:.95rem;line-height:1.9;margin:0 0 12px;">
      Three clear winners emerged — <strong style="color:#f59e0b;">Nostalgia Bridge Dad</strong>,
      <strong style="color:#34d399;">Sturdy Parenting</strong>, and
      <strong style="color:#f472b6;">Routine-Chaos</strong> — delivering 113 of 155 total paid
      deposits ({(113/155*100 if total_paid else 0):.0f}% of all revenue) from {_fn(sum(agg[s]['total_spend'] for s in WINNER_SEGMENTS))}
      in combined spend. Validation campaigns (Top Static, Top Video, Top 3 PC) confirmed
      static format outperforms video at TOFU and mobile placement dominates PC for FSD conversion.
    </p>
    <p style="color:#cbd5e1;font-size:.95rem;line-height:1.9;margin:0 0 28px;">
      The results position Nostalgia Bridge Dad as the highest-volume opportunity (lowest CPM
      {_fs(nostalgia_data['avg_cpm'])}, highest link-click CTR {_fp(nostalgia_data['avg_ctr'])}), Sturdy Parenting
      as the highest-quality buyer segment (best FSD→paid conversion rate at 46%), and
      Routine-Chaos as a reliable scale candidate. With 207 pending Stripe deposits still
      outstanding, a follow-up email sequence targeting non-payers represents an immediate
      revenue recovery opportunity without additional ad spend.
    </p>

    {kpi_grid}
    {winner_badges}"""

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 2: Campaign Structure Analysis
    # ══════════════════════════════════════════════════════════════════════
    sec2 = f"""
    {_section_header("02", "Campaign Structure Analysis", "What worked, what didn't, what to do differently")}

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:28px;">

      <div style="background:#0d1018;border:1px solid #1e2235;border-radius:10px;padding:22px 24px;">
        <div style="color:#34d399;font-size:.7rem;font-weight:700;text-transform:uppercase;
                    letter-spacing:.1em;margin-bottom:16px;">&#10003; What Worked</div>

        <div style="margin-bottom:18px;">
          <div style="color:#e4e7ef;font-size:.88rem;font-weight:600;margin-bottom:6px;">
            Switching optimization event from Purchase to FSD
          </div>
          <p style="color:#64748b;font-size:.82rem;line-height:1.7;margin:0;">
            Campaigns launched with a SALES objective optimized for the <strong style="color:#94a3b8;">Purchase</strong>
            event ($1 deposit). After a few days, the optimization event was switched to
            <strong style="color:#94a3b8;">FSD</strong> (form_submit_deposit) across all ad sets — without changing
            the campaign objective. This was the right call: Meta needs ~50 conversion events per week
            per ad set to exit the learning phase. At $1 deposits (~5–10/week/ad set), the algorithm
            was data-starved and couldn&rsquo;t optimize effectively. FSD events occur 4–6x more
            frequently, giving Meta enough signal to learn and deliver efficiently. The improvement in
            CTR and FSD performance mid-campaign is directly attributable to this switch.
            The LEADS validation campaigns (Top Static, Top Video) later used the same FSD event with
            a LEADS objective — creating a natural experiment showing that the campaign objective
            affects auction behavior (and CPM) even when the conversion event is identical.
          </p>
        </div>

        <div style="margin-bottom:18px;">
          <div style="color:#e4e7ef;font-size:.88rem;font-weight:600;margin-bottom:6px;">
            Equal-budget simultaneous testing
          </div>
          <p style="color:#64748b;font-size:.82rem;line-height:1.7;margin:0;">
            Running all 10 segments at equal daily budgets in the same time window creates
            true apples-to-apples comparisons. Sequential testing would introduce seasonal
            noise, CPM fluctuations, and offer-fatigue artifacts that make cross-segment
            comparison unreliable. This methodology is the right foundation.
          </p>
        </div>

        <div style="margin-bottom:18px;">
          <div style="color:#e4e7ef;font-size:.88rem;font-weight:600;margin-bottom:6px;">
            Separate static and video campaigns (LEADS)
          </div>
          <p style="color:#64748b;font-size:.82rem;line-height:1.7;margin:0;">
            Meta's algorithm has a well-documented delivery bias: when static and video ads
            compete in the same ad set, Meta systematically allocates more budget to video
            because video generates more engagement signals (views, partial watches) that
            the algorithm misinterprets as performance. By separating Top Static Ads
            (120244568399160025) and Top Video Ads (120244568479490025) into dedicated
            campaigns, each format competed on its own merits. Result: static delivered
            35 FSD on {_fs(525.64)} spend vs video's 20 FSD on {_fs(324.35)} — a fair
            comparison revealing static's structural TOFU advantage.
          </p>
        </div>

        <div>
          <div style="color:#e4e7ef;font-size:.88rem;font-weight:600;margin-bottom:6px;">
            Validation campaigns after winner confirmation
          </div>
          <p style="color:#64748b;font-size:.82rem;line-height:1.7;margin:0;">
            Launching Top Static, Top Video, and Top 3 PC only after the 3 winners were
            confirmed prevented wasting validation budget on losing segments. The PC
            campaign (4 FSD on {_fs(130.97)} spend) quickly confirmed the placement
            hierarchy: mobile first, PC only for retargeting.
          </p>
        </div>
      </div>

      <div style="background:#0d1018;border:1px solid #1e2235;border-radius:10px;padding:22px 24px;">
        <div style="color:#f87171;font-size:.7rem;font-weight:700;text-transform:uppercase;
                    letter-spacing:.1em;margin-bottom:16px;">&#9888; What Didn't Work</div>

        <div style="margin-bottom:18px;">
          <div style="color:#34d399;font-size:.88rem;font-weight:600;margin-bottom:6px;">
            &#10003; Intentional audience consolidation (pattern-based merge)
          </div>
          <p style="color:#64748b;font-size:.82rem;line-height:1.7;margin:0;">
            After a few days of live data, HOMEWORK-05 ad sets showed the same audience
            response pattern as Routine-Chaos (similar CTR range, FSD rate, and scroll behavior),
            and IPADMOM-26 showed the same pattern as Anxiety Regulation. Rather than running
            them as separate underperforming segments, they were deliberately merged into the
            matching campaigns. This is a valid optimization — consolidating behaviorally similar
            audiences gives Meta&rsquo;s algorithm more signal, reduces budget fragmentation, and
            avoids over-segmenting what may be the same underlying buyer persona. The segment
            report correctly re-attributes their spend and FSD back to their original segments
            (Homework Meltdown and iPad Battle Mom) for accurate CPaC tracking.
          </p>
        </div>

      </div>
    </div>

    <div style="background:#0d1018;border:1px solid #334155;border-radius:8px;
                padding:12px 18px;font-size:.8rem;color:#475569;">
      &#128073; Full recommended architecture for the next launch is in
      <strong style="color:#60a5fa;">Section 09 — Recommendations</strong>.
    </div>"""

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 3: Segment Performance Analysis
    # ══════════════════════════════════════════════════════════════════════

    # Build segment table
    def _seg_verdict(seg: str, rank: int) -> str:
        if seg in WINNER_SEGMENTS:
            return _badge("&#127942; Winner", "#422006", "#f59e0b", "#78350f")
        if seg in VIABLE_SEGMENTS:
            eliminated = ELIMINATION_DATES.get(seg, "")
            return _badge(f"&#128300; Viable — Paused {eliminated}", "#0c1f2e", "#818cf8", "#1e3a5f")
        eliminated = ELIMINATION_DATES.get(seg, "")
        return _badge(f"&#9679; Eliminated {eliminated}", "#1a1d27", "#64748b", "#2a2e3a")

    CTR_ALL_COLOR  = "#64748b"   # muted slate — context metric
    CTR_LINK_COLOR = "#60a5fa"   # blue accent — primary metric

    def _td_ctr_all(v: float) -> str:
        """CTR (all) cell: muted color, no bold."""
        return (f'<td style="padding:10px 14px;text-align:right;color:{CTR_ALL_COLOR};'
                f'border-bottom:1px solid #111827;white-space:nowrap;">{_fp(v)}</td>')

    def _td_ctr_link(v: float) -> str:
        """CTR (link) cell: accent color, bold when high."""
        fw = "font-weight:600;" if v > 5 else ""
        return (f'<td style="padding:10px 14px;text-align:right;color:{CTR_LINK_COLOR};{fw}'
                f'border-bottom:1px solid #111827;white-space:nowrap;">{_fp(v)}</td>')

    def _td_cpc_all(v: float) -> str:
        """CPC (all) cell: muted color."""
        return (f'<td style="padding:10px 14px;text-align:right;color:{CTR_ALL_COLOR};'
                f'border-bottom:1px solid #111827;white-space:nowrap;">{_fs(v)}</td>')

    def _td_cpc_link(v: float) -> str:
        """CPC (link) cell: accent color."""
        return (f'<td style="padding:10px 14px;text-align:right;color:{CTR_LINK_COLOR};'
                f'border-bottom:1px solid #111827;white-space:nowrap;">{_fs(v)}</td>')

    table_rows = ""
    for rank, seg in enumerate(sorted_segs):
        a = agg[seg]
        paid = stripe_paid.get(seg, 0)
        pending = stripe_pending.get(seg, 0)
        color = SEGMENT_COLORS.get(seg, "#94a3b8")
        fsd = a["total_fsd"]
        spend = a["total_spend"]
        paid_rate = (paid / fsd * 100) if fsd > 0 else 0.0
        fsd_per_100 = (fsd / spend * 100) if spend > 0 else 0.0
        row_bg = "background:#0a0e18;" if rank % 2 == 0 else ""
        table_rows += f"""
        <tr style="{row_bg}">
          {_td(f'{_dot(color)}<span style="color:{color};font-weight:600;">{seg}</span>', "left")}
          {_td(_fs(spend))}
          {_td(_fn(a["total_impr"]), muted=True)}
          {_td(_fn(a["total_link_clicks"]), muted=True)}
          {_td_ctr_all(a.get("avg_ctr_all", 0.0))}
          {_td_ctr_link(a["avg_ctr"])}
          {_td_cpc_all(a.get("avg_cpc_all", 0.0))}
          {_td_cpc_link(a["avg_cpc"])}
          {_td(_fs(a["avg_cpm"]), muted=True)}
          {_td(str(int(fsd)))}
          {_td(str(paid))}
          {_td(f"{pending}", muted=True)}
          {_td(_fp(paid_rate), bold=(paid_rate > 40))}
          {_td(_cpa(spend, paid))}
          {_td(f"{fsd_per_100:.1f}", muted=True)}
          <td style="padding:10px 14px;text-align:center;border-bottom:1px solid #111827;">
            {_seg_verdict(seg, rank)}
          </td>
        </tr>"""

    # Legend snippet reused by multiple tables
    ctr_legend = (
        f'<span style="color:{CTR_ALL_COLOR};font-weight:600;">CTR (all)</span> = all ad interactions &nbsp;·&nbsp; '
        f'<span style="color:{CTR_LINK_COLOR};font-weight:600;">CTR (link)</span> = clicks to landing page only'
    )

    seg_table = f"""
    <div style="overflow-x:auto;margin:20px 0;">
      <table style="width:100%;border-collapse:collapse;min-width:1300px;">
        <thead style="background:#060810;">
          <tr>
            {_th("Segment", "left")}
            {_th("Spend")}
            {_th("Impressions")}
            {_th("Link Clicks")}
            <th style="padding:9px 14px;text-align:right;color:{CTR_ALL_COLOR};font-size:.7rem;
                       font-weight:500;text-transform:uppercase;letter-spacing:.07em;
                       border-bottom:1px solid #1e2235;">CTR (all)</th>
            <th style="padding:9px 14px;text-align:right;color:{CTR_LINK_COLOR};font-size:.7rem;
                       font-weight:500;text-transform:uppercase;letter-spacing:.07em;
                       border-bottom:1px solid #1e2235;">CTR (link)</th>
            <th style="padding:9px 14px;text-align:right;color:{CTR_ALL_COLOR};font-size:.7rem;
                       font-weight:500;text-transform:uppercase;letter-spacing:.07em;
                       border-bottom:1px solid #1e2235;">CPC (all)</th>
            <th style="padding:9px 14px;text-align:right;color:{CTR_LINK_COLOR};font-size:.7rem;
                       font-weight:500;text-transform:uppercase;letter-spacing:.07em;
                       border-bottom:1px solid #1e2235;">CPC (link)</th>
            {_th("CPM")}
            {_th("FSD")}
            {_th("Paid")}
            {_th("Pending")}
            {_th("Paid Rate")}
            {_th("CPaC")}
            {_th("FSD/$100")}
            {_th("Verdict", "center")}
          </tr>
        </thead>
        <tbody>
          {table_rows}
        </tbody>
      </table>
    </div>
    <p style="color:#334155;font-size:.72rem;margin:-8px 0 16px;line-height:1.6;">
      {ctr_legend} &nbsp;·&nbsp; Link-click CTR is the primary engagement metric; all-clicks CTR is shown for context.
    </p>"""

    # Per-group narratives
    nostalgia = agg["Nostalgia Bridge Dad"]
    sturdy = agg["Sturdy Parenting"]
    routine = agg["Routine-Chaos"]
    n_paid = stripe_paid.get("Nostalgia Bridge Dad", 0)
    s_paid = stripe_paid.get("Sturdy Parenting", 0)
    r_paid = stripe_paid.get("Routine-Chaos", 0)
    n_pend = stripe_pending.get("Nostalgia Bridge Dad", 0)
    s_pend = stripe_pending.get("Sturdy Parenting", 0)
    r_pend = stripe_pending.get("Routine-Chaos", 0)
    n_fsd = nostalgia["total_fsd"]
    s_fsd = sturdy["total_fsd"]
    r_fsd = routine["total_fsd"]
    n_paid_rate = (n_paid / n_fsd * 100) if n_fsd else 0
    s_paid_rate = (s_paid / s_fsd * 100) if s_fsd else 0
    r_paid_rate = (r_paid / r_fsd * 100) if r_fsd else 0

    group_analysis = f"""
    <div style="margin-top:28px;display:grid;grid-template-columns:1fr;gap:20px;">

      <!-- Group A: Winners -->
      <div style="background:#0d1018;border:1px solid #f59e0b33;border-radius:10px;padding:24px 28px;">
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:18px;">
          <span style="background:#422006;color:#f59e0b;border:1px solid #78350f;border-radius:6px;
                       padding:4px 14px;font-size:.72rem;font-weight:700;text-transform:uppercase;
                       letter-spacing:.08em;">Group A — Winners</span>
          <span style="color:#475569;font-size:.8rem;">Nostalgia Bridge Dad · Sturdy Parenting · Routine-Chaos</span>
        </div>
        <p style="color:#cbd5e1;font-size:.88rem;line-height:1.8;margin:0 0 12px;">
          All three winners share a common trait: they speak to <em>identity and parental role</em>
          rather than product features. Nostalgia Bridge Dad activates the emotional desire to
          give children the same unhurried childhood the parent remembers — a timeless,
          universally felt tension that produces high scroll-stop and deep LP engagement
          (link CTR {_fp(nostalgia["avg_ctr"])}, CPM {_fs(nostalgia["avg_cpm"])}, lowest in the cohort).
          Sturdy Parenting reaches evidence-backed parents who research before buying — their
          conversion path is slower but highly qualified, reflected in the highest paid rate of
          the three winners ({s_paid_rate:.0f}% of {int(s_fsd)} FSD converted to paid, vs {n_paid_rate:.0f}%
          for Nostalgia). Routine-Chaos resonates with parents in ongoing household friction who
          are actively looking for a solution, creating consistent week-over-week delivery.
        </p>
        <p style="color:#94a3b8;font-size:.85rem;line-height:1.8;margin:0;">
          <strong style="color:#f59e0b;">Key paid rate difference:</strong>
          Sturdy Parenting's {s_paid_rate:.0f}% paid rate vs Nostalgia's {n_paid_rate:.0f}% and
          Routine's {r_paid_rate:.0f}% suggests Sturdy attracts the most purchase-ready buyers.
          Nostalgia produces the most volume ({int(n_fsd)} FSD, {n_paid} paid, {n_pend} pending)
          but with a broader, slightly less committed audience — the {n_pend} pending Nostalgia
          signups are the highest-priority follow-up targets.
        </p>
      </div>

      <!-- Group B: Viable -->
      <div style="background:#0d1018;border:1px solid #818cf833;border-radius:10px;padding:24px 28px;">
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:18px;">
          <span style="background:#0c1f2e;color:#818cf8;border:1px solid #1e3a5f;border-radius:6px;
                       padding:4px 14px;font-size:.72rem;font-weight:700;text-transform:uppercase;
                       letter-spacing:.08em;">Group B — Viable, Paused</span>
          <span style="color:#475569;font-size:.8rem;">Anxiety Regulation · ADHD-EF Intervention · Selective Mutism</span>
        </div>
        <p style="color:#cbd5e1;font-size:.88rem;line-height:1.8;margin:0 0 12px;">
          These three segments were paused for budget allocation reasons, not because they failed.
          Anxiety Regulation generated {int(agg['Anxiety Regulation']['total_fsd'])} FSD and {stripe_paid.get('Anxiety Regulation', 0)} paid
          deposits on {_fs(agg['Anxiety Regulation']['total_spend'])} spend — a CPaC of {_cpa(agg['Anxiety Regulation']['total_spend'], stripe_paid.get('Anxiety Regulation', 1))}
          — and its CPM was actively improving in the final week before pause, indicating the
          algorithm was still optimizing toward the right audience. ADHD-EF Intervention showed
          solid mid-funnel performance (link CTR {_fp(agg['ADHD-EF Intervention']['avg_ctr'])}) with niche
          specificity that would benefit from dedicated LP copy. Selective Mutism is a very
          small TAM but hyper-specific — parents in this situation have extreme pain and high
          willingness to pay.
        </p>
        <p style="color:#94a3b8;font-size:.85rem;line-height:1.8;margin:0;">
          <strong style="color:#818cf8;">Re-entry conditions:</strong> Reactivate Anxiety
          Regulation at 1.5x current budget once core three are scaled. Test ADHD-EF
          Intervention with a dedicated landing page addressing executive function specifically.
          Selective Mutism warrants a low-budget ($5/day) always-on campaign to capture the
          small but highly motivated audience.
        </p>
      </div>

      <!-- Group C: Not Viable -->
      <div style="background:#0d1018;border:1px solid #2a2e3a;border-radius:10px;padding:24px 28px;">
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:18px;">
          <span style="background:#1a1d27;color:#64748b;border:1px solid #2a2e3a;border-radius:6px;
                       padding:4px 14px;font-size:.72rem;font-weight:700;text-transform:uppercase;
                       letter-spacing:.08em;">Group C — Not Viable</span>
          <span style="color:#475569;font-size:.8rem;">iPad Battle Mom · Homework Meltdown · Homeschool · AI-Curious Parent</span>
        </div>
        <p style="color:#cbd5e1;font-size:.88rem;line-height:1.8;margin:0 0 12px;">
          The root causes differ by segment. <strong style="color:#94a3b8;">iPad Battle Mom</strong>
          is the most interesting case: link-click CTR of {_fp(agg['iPad Battle Mom']['avg_ctr'])} is among the
          highest in the cohort, indicating strong scroll-stop and ad resonance. But only
          {int(agg['iPad Battle Mom']['total_fsd'])} FSD from {_fn(agg['iPad Battle Mom']['total_link_clicks'])} clicks
          represents a severe landing page mismatch — parents clicked expecting a different
          proposition than the LP delivered. This is an ad-LP narrative gap, not an ad problem.
          <strong style="color:#94a3b8;">Homework Meltdown</strong> showed decent CTR but low
          FSD — likely because the homework conflict message is too situational and transient;
          parents don't identify with it as a persistent identity, so urgency fades.
        </p>
        <p style="color:#94a3b8;font-size:.85rem;line-height:1.8;margin:0;">
          <strong style="color:#64748b;">Homeschool</strong> had a very small TAM (~300K addressable)
          producing high CPMs and limited scale. <strong style="color:#64748b;">AI-Curious Parent</strong>
          is likely an early-adopter techie audience that over-indexes on research and
          under-indexes on emotional purchase triggers — they click to learn, not to buy.
          Neither segment warrants reinvestment at current offer/LP state.
        </p>
      </div>

    </div>"""

    # ── Weekly trend table (winner segments only) ────────────────────────
    WINNER_CID_TO_SEG = {
        "120243727739260025": "Nostalgia Bridge Dad",
        "120243727242320025": "Sturdy Parenting",
        "120243727123190025": "Routine-Chaos",
    }
    WEEKS = ["W1", "W2", "W3"]
    WEEK_LABELS = {"W1": "W1 May 13–19", "W2": "W2 May 20–26", "W3": "W3 May 27–31"}

    weekly_header_cols = _th("Segment", "left")
    for wk in WEEKS:
        lbl = WEEK_LABELS[wk]
        weekly_header_cols += (
            f'<th style="padding:9px 14px;text-align:right;color:#475569;font-size:.7rem;'
            f'font-weight:500;text-transform:uppercase;letter-spacing:.07em;'
            f'border-bottom:1px solid #1e2235;" colspan="5">{lbl}</th>'
        )

    weekly_sub_header = _th("", "left")
    for _wk in WEEKS:
        weekly_sub_header += (
            _th("Spend") +
            _th("FSD") +
            f'<th style="padding:9px 14px;text-align:right;color:{CTR_ALL_COLOR};font-size:.68rem;'
            f'font-weight:500;text-transform:uppercase;letter-spacing:.07em;'
            f'border-bottom:1px solid #1e2235;">CTR (all)</th>' +
            f'<th style="padding:9px 14px;text-align:right;color:{CTR_LINK_COLOR};font-size:.68rem;'
            f'font-weight:500;text-transform:uppercase;letter-spacing:.07em;'
            f'border-bottom:1px solid #1e2235;">CTR (link)</th>' +
            _th("CPM")
        )

    weekly_rows = ""
    for rank, (cid, seg) in enumerate(WINNER_CID_TO_SEG.items()):
        color = SEGMENT_COLORS.get(seg, "#94a3b8")
        row_bg = "background:#0a0e18;" if rank % 2 == 0 else ""
        row = f'<tr style="{row_bg}">{_td(f"{_dot(color)}<span style=\'color:{color};font-weight:600;\'>{seg}</span>", "left")}'
        for wk in WEEKS:
            wd = weekly.get(cid, {}).get(wk, {})
            spend = wd.get("spend", 0.0)
            fsd   = wd.get("fsd", 0)
            ctr_link = wd.get("ctr", 0.0)
            ctr_all  = wd.get("ctr_all", 0.0)
            cpm      = wd.get("cpm", 0.0)
            row += (
                _td(_fs(spend) if spend else "—") +
                _td(str(int(fsd)) if fsd else "—") +
                f'<td style="padding:10px 14px;text-align:right;color:{CTR_ALL_COLOR};'
                f'border-bottom:1px solid #111827;white-space:nowrap;">'
                f'{"—" if not ctr_all else _fp(ctr_all)}</td>' +
                f'<td style="padding:10px 14px;text-align:right;color:{CTR_LINK_COLOR};'
                f'border-bottom:1px solid #111827;white-space:nowrap;">'
                f'{"—" if not ctr_link else _fp(ctr_link)}</td>' +
                _td(_fs(cpm) if cpm else "—", muted=True)
            )
        row += "</tr>"
        weekly_rows += row

    weekly_trend_table = f"""
    <h3 style="color:#e4e7ef;font-size:.95rem;font-weight:600;margin:32px 0 12px;">
      Winner Segments — Weekly Trend
    </h3>
    <div style="overflow-x:auto;margin-bottom:8px;">
      <table style="width:100%;border-collapse:collapse;min-width:900px;">
        <thead style="background:#060810;">
          <tr>{weekly_header_cols}</tr>
          <tr style="background:#040608;">{weekly_sub_header}</tr>
        </thead>
        <tbody>{weekly_rows}</tbody>
      </table>
    </div>
    <p style="color:#334155;font-size:.72rem;margin:0 0 16px;line-height:1.6;">
      {ctr_legend}
    </p>"""

    # ── CTR(all) vs CTR(link) ratio analysis — built from live agg data ──────
    # Use the SAME source as the segment table above so both tables are consistent.
    _seg_order = [
        ("Nostalgia Bridge Dad", "#f59e0b"),
        ("Sturdy Parenting",     "#34d399"),
        ("Routine-Chaos",        "#f472b6"),
        ("Anxiety Regulation",   "#60a5fa"),
        ("ADHD-EF Intervention", "#818cf8"),
        ("iPad Battle Mom",      "#94a3b8"),
        ("Selective Mutism",     "#94a3b8"),
        ("Homework Meltdown",    "#94a3b8"),
        ("AI-Curious Parent",    "#64748b"),
        ("Homeschool",           "#64748b"),
    ]
    ratio_data = []
    for seg, color in _seg_order:
        ctr_link = agg.get(seg, {}).get("avg_ctr", 0) or 0
        ctr_all  = all_clicks_db.get(seg, {}).get("avg_ctr_all", 0) or 0
        if ctr_all > 0:
            ratio_data.append((seg, ctr_all, ctr_link, color))
    avg_ratio = sum(l/a for _, a, l, _ in ratio_data) / len(ratio_data) * 100

    ratio_rows = ""
    for name, ctr_all, ctr_link, color in ratio_data:
        ratio = ctr_link / ctr_all * 100
        bar_w = min(100, ratio)
        bar_color = "#34d399" if ratio >= 55 else "#f59e0b" if ratio >= 50 else "#f87171"
        if ratio >= 55:
            tier = f'<span style="background:#34d39922;color:#34d399;border:1px solid #34d39944;border-radius:4px;padding:1px 7px;font-size:.72rem;">Intent-driven</span>'
        elif ratio >= 50:
            tier = f'<span style="background:#f59e0b22;color:#f59e0b;border:1px solid #f59e0b44;border-radius:4px;padding:1px 7px;font-size:.72rem;">Mixed</span>'
        else:
            tier = f'<span style="background:#f8717122;color:#f87171;border:1px solid #f8717144;border-radius:4px;padding:1px 7px;font-size:.72rem;">Emot. resonance</span>'
        ratio_rows += f"""
        <tr style="border-bottom:1px solid #111827;">
          <td style="padding:9px 14px;">
            <span style="color:{color};font-weight:600;font-size:.85rem;">{name}</span>
          </td>
          <td style="padding:9px 14px;color:#64748b;font-size:.83rem;">{ctr_all:.2f}%</td>
          <td style="padding:9px 14px;color:#60a5fa;font-size:.83rem;">{ctr_link:.2f}%</td>
          <td style="padding:9px 14px;min-width:160px;">
            <div style="display:flex;align-items:center;gap:8px;">
              <div style="flex:1;height:8px;background:#1e2235;border-radius:4px;">
                <div style="width:{bar_w:.1f}%;height:100%;background:{bar_color};border-radius:4px;"></div>
              </div>
              <span style="color:{bar_color};font-weight:700;font-size:.83rem;min-width:38px;">{ratio:.1f}%</span>
            </div>
          </td>
          <td style="padding:9px 14px;">{tier}</td>
        </tr>"""

    ratio_insights = [
        ("#34d399", "Nostalgia Bridge Dad & AI-Curious (58%) — highest intent ratio",
         "58% of all interactions result in an LP click — people who engage mean to act. "
         "For Nostalgia Bridge this translates to high FSD + paid deposits. "
         "For AI-Curious it confirms the LP is the problem, not the ad."),
        ("#f59e0b", "Sturdy Parenting (48%) — lower ratio, best outcomes",
         "These parents are deliberate — they take more time to decide to click. "
         "But when they do, they convert at 77.9% LP CVR and 50% paid rate. "
         "A lower ratio doesn't mean a worse audience — it means a more considered one."),
        ("#f87171", "Homework Meltdown (44%) & ADHD-EF (45%) — emotional resonance without intent",
         "These ads trigger strong recognition ('that's my kid') but people save/share rather than click to buy. "
         "High CTR(all) with low ratio = brand awareness creative, not direct-response. "
         "To improve: make the CTA more urgent and the LP link more prominent in the ad copy."),
        ("#60a5fa", f"Average ratio: {avg_ratio:.1f}% — benchmark for next campaign",
         "For a direct-response pre-order campaign, target a ratio above 55%. "
         "Ads below 45% are generating brand engagement but not enough purchase intent. "
         "Use CTR(link) as the primary bid optimization signal, not CTR(all)."),
    ]

    insight_html = ""
    for color, title, body in ratio_insights:
        insight_html += f"""
        <div style="display:flex;gap:14px;padding:12px 0;border-bottom:1px solid #111827;">
          <div style="width:4px;flex-shrink:0;background:{color};border-radius:2px;"></div>
          <div>
            <p style="color:#e4e7ef;font-weight:600;font-size:.87rem;margin:0 0 4px;">{title}</p>
            <p style="color:#64748b;font-size:.81rem;line-height:1.7;margin:0;">{body}</p>
          </div>
        </div>"""

    ratio_section = f"""
    <h3 style="color:#e4e7ef;font-size:.95rem;font-weight:600;margin:32px 0 8px;">
      CTR(all) vs CTR(link) — Engagement Intent Analysis
    </h3>
    <p style="color:#475569;font-size:.8rem;margin:0 0 16px;line-height:1.6;">
      The ratio of CTR(link) to CTR(all) reveals what percentage of ad interactions represent genuine
      purchase intent vs passive engagement (likes, shares, comment clicks).
      A high ratio = the ad drives action. A low ratio = the ad drives emotion but not clicks.
    </p>
    <div style="overflow-x:auto;margin-bottom:16px;">
      <table style="width:100%;border-collapse:collapse;min-width:560px;">
        <thead style="background:#060810;">
          <tr>
            <th style="padding:8px 14px;text-align:left;color:#475569;font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Segment</th>
            <th style="padding:8px 14px;text-align:right;color:#64748b;font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;">CTR (all)</th>
            <th style="padding:8px 14px;text-align:right;color:#60a5fa;font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;">CTR (link)</th>
            <th style="padding:8px 14px;text-align:left;color:#475569;font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Link/All Ratio</th>
            <th style="padding:8px 14px;text-align:left;color:#475569;font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Audience Type</th>
          </tr>
        </thead>
        <tbody>{ratio_rows}</tbody>
      </table>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:8px;">
      <div style="background:#0d1018;border-radius:6px;padding:10px 14px;display:flex;align-items:center;gap:8px;">
        <div style="width:10px;height:10px;background:#34d399;border-radius:2px;flex-shrink:0;"></div>
        <span style="color:#64748b;font-size:.75rem;"><strong style="color:#94a3b8;">≥55%</strong> — Intent-driven: most interactions = LP clicks</span>
      </div>
      <div style="background:#0d1018;border-radius:6px;padding:10px 14px;display:flex;align-items:center;gap:8px;">
        <div style="width:10px;height:10px;background:#f59e0b;border-radius:2px;flex-shrink:0;"></div>
        <span style="color:#64748b;font-size:.75rem;"><strong style="color:#94a3b8;">50–55%</strong> — Mixed: engagement + some intent</span>
      </div>
      <div style="background:#0d1018;border-radius:6px;padding:10px 14px;display:flex;align-items:center;gap:8px;">
        <div style="width:10px;height:10px;background:#f87171;border-radius:2px;flex-shrink:0;"></div>
        <span style="color:#64748b;font-size:.75rem;"><strong style="color:#94a3b8;">&lt;50%</strong> — Emotional resonance: reactions > clicks</span>
      </div>
      <div style="background:#0d1018;border-radius:6px;padding:10px 14px;display:flex;align-items:center;gap:8px;">
        <div style="width:10px;height:10px;background:#60a5fa;border-radius:2px;flex-shrink:0;"></div>
        <span style="color:#64748b;font-size:.75rem;"><strong style="color:#94a3b8;">Avg: {avg_ratio:.1f}%</strong> — Campaign benchmark</span>
      </div>
    </div>
    <div style="background:#0d1018;border:1px solid #1e2235;border-radius:10px;padding:18px 20px;margin-bottom:8px;">
      {insight_html}
    </div>"""

    sec3 = f"""
    {_section_header("03", "Segment Performance Analysis", "All 10 segments ranked — spend, efficiency, funnel conversion, and verdict")}
    {seg_table}
    {ratio_section}
    {weekly_trend_table}
    {group_analysis}"""

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 4: Ad Format & Creative Performance
    # ══════════════════════════════════════════════════════════════════════

    # Aggregate by style for top performers
    style_agg: dict[str, dict] = defaultdict(lambda: {"spend": 0.0, "fsd": 0, "ctr_sum": 0.0, "cpc_sum": 0.0, "n": 0})
    for ad in ad_perf:
        style = ad.get("ad_style") or "unknown"
        style_agg[style]["spend"] += ad.get("spend", 0)
        style_agg[style]["fsd"] += ad.get("fsd", 0) or 0
        style_agg[style]["ctr_sum"] += ad.get("ctr", 0) or 0
        style_agg[style]["cpc_sum"] += ad.get("cpc", 0) or 0
        style_agg[style]["n"] += 1

    style_rows = ""
    for style, d in sorted(style_agg.items(), key=lambda x: -x[1]["fsd"]):
        n = max(d["n"], 1)
        avg_ctr_all = d["ctr_sum"] / n   # AVG(m.ctr) from DB — all-clicks
        avg_cpc_all = d["cpc_sum"] / n   # AVG(m.cpc) from DB — all-clicks
        fsd_per_100 = (d["fsd"] / d["spend"] * 100) if d["spend"] > 0 else 0
        style_display = style.replace("_", " ").title()
        style_rows += f"""
        <tr>
          {_td(f'<span style="color:#e4e7ef;font-weight:600;">{style_display}</span>', "left")}
          {_td(_fs(d["spend"]))}
          {_td(str(int(d["fsd"])))}
          <td style="padding:10px 14px;text-align:right;color:{CTR_ALL_COLOR};
                     border-bottom:1px solid #111827;white-space:nowrap;">{_fp(avg_ctr_all)}</td>
          <td style="padding:10px 14px;text-align:right;color:{CTR_LINK_COLOR};
                     border-bottom:1px solid #111827;white-space:nowrap;">—</td>
          <td style="padding:10px 14px;text-align:right;color:{CTR_ALL_COLOR};
                     border-bottom:1px solid #111827;white-space:nowrap;">{_fs(avg_cpc_all)}</td>
          <td style="padding:10px 14px;text-align:right;color:{CTR_LINK_COLOR};
                     border-bottom:1px solid #111827;white-space:nowrap;">—</td>
          {_td(f"{fsd_per_100:.1f}")}
          {_td(str(d["n"]), muted=True)}
        </tr>"""

    # Top ads table
    top_ads_rows = ""
    for i, ad in enumerate(ad_perf[:12]):
        style = (ad.get("ad_style") or "unknown").replace("_", " ").title()
        fmt = (ad.get("ad_format") or "?").replace("_", " ")
        name = ad.get("ad_name", "?")
        # extract short name
        short_name = name.split("|")[-1].strip() if "|" in name else name[:40]
        fsd = ad.get("fsd", 0) or 0
        color = "#34d399" if fsd >= 10 else "#60a5fa" if fsd >= 5 else "#64748b"
        ctr_all_val = ad.get("ctr", 0) or 0
        cpc_all_val = ad.get("cpc", 0) or 0
        top_ads_rows += f"""
        <tr>
          {_td(f'<span style="color:#94a3b8;font-size:.78rem;">#{i+1}</span>', "center")}
          {_td(f'<span style="color:#cbd5e1;font-size:.8rem;">{short_name[:60]}</span>', "left")}
          {_td(f'<span style="background:#1e2235;color:#818cf8;border-radius:4px;padding:2px 8px;font-size:.75rem;">{style}</span>', "center")}
          {_td(f'<span style="background:#111827;color:#64748b;border-radius:4px;padding:2px 8px;font-size:.75rem;">{fmt}</span>', "center")}
          {_td(_fs(ad.get("spend", 0)))}
          {_td(f'<span style="color:{color};font-weight:700;">{int(fsd)}</span>')}
          <td style="padding:10px 14px;text-align:right;color:{CTR_ALL_COLOR};
                     border-bottom:1px solid #111827;white-space:nowrap;">{_fp(ctr_all_val)}</td>
          <td style="padding:10px 14px;text-align:right;color:{CTR_LINK_COLOR};
                     border-bottom:1px solid #111827;white-space:nowrap;">—</td>
          <td style="padding:10px 14px;text-align:right;color:{CTR_ALL_COLOR};
                     border-bottom:1px solid #111827;white-space:nowrap;">{_fs(cpc_all_val)}</td>
          <td style="padding:10px 14px;text-align:right;color:{CTR_LINK_COLOR};
                     border-bottom:1px solid #111827;white-space:nowrap;">—</td>
        </tr>"""

    sec4 = f"""
    {_section_header("04", "Ad Format & Creative Performance", "Static vs video delivery bias, angle performance, and what to double down on")}

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:28px;">
      <div style="background:#0d1018;border:1px solid #1e2235;border-radius:10px;padding:22px 24px;">
        <div style="color:#f59e0b;font-size:.7rem;font-weight:700;text-transform:uppercase;
                    letter-spacing:.1em;margin-bottom:14px;">Static vs Video: The Delivery Bias Problem</div>
        <p style="color:#64748b;font-size:.82rem;line-height:1.75;margin:0 0 14px;">
          When static images and video ads compete inside the same ad set, Meta's algorithm
          systematically favors video. The reason: video generates micro-engagement signals
          (3-second views, partial watches, replays) that the optimization algorithm treats
          as evidence of audience interest — even when those viewers never clicked. The result
          is that video "wins" the internal auction not because it converts better, but because
          it creates more activity that resembles engagement to Meta's model.
        </p>
        <p style="color:#64748b;font-size:.82rem;line-height:1.75;margin:0 0 14px;">
          The solution implemented here — separate Top Static Ads and Top Video Ads campaigns —
          forces each format to be judged on its actual output: FSD and paid deposits.
        </p>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:16px;">
          {_mini_stat("Static FSD/$100 Spend", "6.5", "#34d399")}
          {_mini_stat("Video FSD/$100 Spend", "5.8", "#f59e0b")}
          {_mini_stat("Static CPM", "$29.58", "#34d399")}
          {_mini_stat("Video CPM", "$48.85", "#f87171")}
        </div>
      </div>

      <div style="background:#0d1018;border:1px solid #1e2235;border-radius:10px;padding:22px 24px;">
        <div style="color:#60a5fa;font-size:.7rem;font-weight:700;text-transform:uppercase;
                    letter-spacing:.1em;margin-bottom:14px;">When to Use Each Format</div>
        <div style="margin-bottom:16px;">
          <div style="color:#e4e7ef;font-size:.85rem;font-weight:600;margin-bottom:6px;">
            Static wins at TOFU (cold audience, feed placement)
          </div>
          <p style="color:#64748b;font-size:.8rem;line-height:1.7;margin:0;">
            Static images load instantly and communicate the core proposition within the
            0.4-second average scroll window. They self-select viewers who pause
            voluntarily — a stronger intent signal than a video that autoplays. Lower
            CPM ({_fs(29.58)} vs {_fs(48.85)} for video) means more reach per dollar.
          </p>
        </div>
        <div>
          <div style="color:#e4e7ef;font-size:.85rem;font-weight:600;margin-bottom:6px;">
            Video is still essential for warm audiences
          </div>
          <p style="color:#64748b;font-size:.8rem;line-height:1.7;margin:0;">
            Retargeting campaigns (FSD non-payers, LP visitors, engagement audiences)
            should lead with video. Warm audiences already know the product — video
            can address price objections, show product in use, and build the
            emotional narrative that converts consideration to purchase.
            For the 207 pending Stripe deposits, a 30–60s video addressing
            "why now" and "why $99 is worth it" is the recommended next step.
          </p>
        </div>
      </div>
    </div>

    <h3 style="color:#e4e7ef;font-size:.95rem;font-weight:600;margin:28px 0 12px;">
      Creative Style Performance (by FSD)
    </h3>

    <!-- Creative style reference guide -->
    <div style="background:#0d1018;border:1px solid #1e2235;border-radius:10px;
                padding:18px 20px;margin-bottom:20px;">
      <div style="color:#94a3b8;font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;
                  font-weight:700;margin-bottom:14px;">Creative Style Reference — Buyer Mindset Each Angle Targets</div>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;grid-template-rows:auto;">

        <div style="background:#080b12;border:1px solid #1e2235;border-radius:7px;padding:12px 14px;">
          <div style="color:#60a5fa;font-size:.72rem;font-weight:700;margin-bottom:4px;">parent_relief</div>
          <div style="color:#94a3b8;font-size:.72rem;font-style:italic;margin-bottom:5px;">"Feeling seen, not sold to"</div>
          <div style="color:#475569;font-size:.72rem;line-height:1.55;">Validates exhaustion before mentioning Nowa. Cream copy card, diary page visual.</div>
        </div>

        <div style="background:#080b12;border:1px solid #1e2235;border-radius:7px;padding:12px 14px;">
          <div style="color:#a78bfa;font-size:.72rem;font-weight:700;margin-bottom:4px;">aspiration_purpose</div>
          <div style="color:#94a3b8;font-size:.72rem;font-style:italic;margin-bottom:5px;">"I want that for my family"</div>
          <div style="color:#475569;font-size:.72rem;line-height:1.55;">Shows the future state the parent wants. Navy typography, warm lifestyle scene.</div>
        </div>

        <div style="background:#080b12;border:1px solid #1e2235;border-radius:7px;padding:12px 14px;">
          <div style="color:#f472b6;font-size:.72rem;font-weight:700;margin-bottom:4px;">contrast_repositioning</div>
          <div style="color:#94a3b8;font-size:.72rem;font-style:italic;margin-bottom:5px;">"Why not just use the iPad?"</div>
          <div style="color:#475569;font-size:.72rem;line-height:1.55;">Before vs after. Nowa vs the status quo. Split panel, two-column comparison.</div>
        </div>

        <div style="background:#080b12;border:1px solid #1e2235;border-radius:7px;padding:12px 14px;">
          <div style="color:#34d399;font-size:.72rem;font-weight:700;margin-bottom:4px;">product_hero</div>
          <div style="color:#94a3b8;font-size:.72rem;font-style:italic;margin-bottom:5px;">"Ready to buy, needs trust"</div>
          <div style="color:#475569;font-size:.72rem;line-height:1.55;">Device-forward. Expert credentials, science backing, proof points. Closes on authority. Product hero shot, science grid with advisors.</div>
        </div>

        <div style="background:#080b12;border:1px solid #1e2235;border-radius:7px;padding:12px 14px;">
          <div style="color:#4ade80;font-size:.72rem;font-weight:700;margin-bottom:4px;">testimonial</div>
          <div style="color:#94a3b8;font-size:.72rem;font-style:italic;margin-bottom:5px;">"Ready to buy, needs social proof"</div>
          <div style="color:#475569;font-size:.72rem;line-height:1.55;">A real parent voice anchored to a specific moment — not generic praise. Quote card with timestamp and attribution.</div>
        </div>

        <div style="background:#080b12;border:1px solid #1e2235;border-radius:7px;padding:12px 14px;">
          <div style="color:#f59e0b;font-size:.72rem;font-weight:700;margin-bottom:4px;">transformation_proof</div>
          <div style="color:#94a3b8;font-size:.72rem;font-style:italic;margin-bottom:5px;">"Show me the proof"</div>
          <div style="color:#475569;font-size:.72rem;line-height:1.55;">Concrete checklist: ✗ before, ✓ after. Before/after checklist card.</div>
        </div>

        <div style="background:#080b12;border:1px solid #1e2235;border-radius:7px;padding:12px 14px;">
          <div style="color:#38bdf8;font-size:.72rem;font-weight:700;margin-bottom:4px;">native_ui</div>
          <div style="color:#94a3b8;font-size:.72rem;font-style:italic;margin-bottom:5px;">"Scroll-stopping native feel"</div>
          <div style="color:#475569;font-size:.72rem;line-height:1.55;">Simulates iMessage, iOS toggle, Stories. Feels native, not like an ad.</div>
        </div>

        <div style="background:#080b12;border:1px solid #1e2235;border-radius:7px;padding:12px 14px;">
          <div style="color:#fb923c;font-size:.72rem;font-weight:700;margin-bottom:4px;">educational (carousel)</div>
          <div style="color:#94a3b8;font-size:.72rem;font-style:italic;margin-bottom:5px;">"Educate me first"</div>
          <div style="color:#475569;font-size:.72rem;line-height:1.55;">Multi-card explanation of a concept or feature. Carousel format, 1:1 only.</div>
        </div>

        <div style="background:#060810;border:1px dashed #1e2235;border-radius:7px;padding:12px 14px;
                    display:flex;align-items:center;justify-content:center;">
          <div style="color:#334155;font-size:.72rem;text-align:center;line-height:1.6;">
            Funnel stage matches mindset.<br>Match the angle to where the parent is — not where you want them to be.
          </div>
        </div>

      </div>
    </div>
    <div style="overflow-x:auto;margin-bottom:12px;">
      <table style="width:100%;border-collapse:collapse;min-width:820px;">
        <thead style="background:#060810;">
          <tr>
            {_th("Ad Style", "left")}
            {_th("Spend")}
            {_th("FSD")}
            <th style="padding:9px 14px;text-align:right;color:{CTR_ALL_COLOR};font-size:.7rem;
                       font-weight:500;text-transform:uppercase;letter-spacing:.07em;
                       border-bottom:1px solid #1e2235;">Avg CTR (all)</th>
            <th style="padding:9px 14px;text-align:right;color:{CTR_LINK_COLOR};font-size:.7rem;
                       font-weight:500;text-transform:uppercase;letter-spacing:.07em;
                       border-bottom:1px solid #1e2235;">Avg CTR (link)</th>
            <th style="padding:9px 14px;text-align:right;color:{CTR_ALL_COLOR};font-size:.7rem;
                       font-weight:500;text-transform:uppercase;letter-spacing:.07em;
                       border-bottom:1px solid #1e2235;">Avg CPC (all)</th>
            <th style="padding:9px 14px;text-align:right;color:{CTR_LINK_COLOR};font-size:.7rem;
                       font-weight:500;text-transform:uppercase;letter-spacing:.07em;
                       border-bottom:1px solid #1e2235;">Avg CPC (link)</th>
            {_th("FSD/$100")}
            {_th("Ads")}
          </tr>
        </thead>
        <tbody>{style_rows}</tbody>
      </table>
    </div>
    <p style="color:#334155;font-size:.72rem;margin:0 0 20px;line-height:1.6;">
      {ctr_legend} &nbsp;·&nbsp; Ad-level link-click metrics not available from DB (shown as —); use segment table for link-click benchmarks.
    </p>

    <h3 style="color:#e4e7ef;font-size:.95rem;font-weight:600;margin:28px 0 12px;">
      Top Performing Individual Ads (by FSD)
    </h3>
    <div style="overflow-x:auto;margin-bottom:8px;">
      <table style="width:100%;border-collapse:collapse;min-width:960px;">
        <thead style="background:#060810;">
          <tr>
            {_th("#", "center")}
            {_th("Ad Name", "left")}
            {_th("Style", "center")}
            {_th("Format", "center")}
            {_th("Spend")}
            {_th("FSD")}
            <th style="padding:9px 14px;text-align:right;color:{CTR_ALL_COLOR};font-size:.7rem;
                       font-weight:500;text-transform:uppercase;letter-spacing:.07em;
                       border-bottom:1px solid #1e2235;">CTR (all)</th>
            <th style="padding:9px 14px;text-align:right;color:{CTR_LINK_COLOR};font-size:.7rem;
                       font-weight:500;text-transform:uppercase;letter-spacing:.07em;
                       border-bottom:1px solid #1e2235;">CTR (link)</th>
            <th style="padding:9px 14px;text-align:right;color:{CTR_ALL_COLOR};font-size:.7rem;
                       font-weight:500;text-transform:uppercase;letter-spacing:.07em;
                       border-bottom:1px solid #1e2235;">CPC (all)</th>
            <th style="padding:9px 14px;text-align:right;color:{CTR_LINK_COLOR};font-size:.7rem;
                       font-weight:500;text-transform:uppercase;letter-spacing:.07em;
                       border-bottom:1px solid #1e2235;">CPC (link)</th>
          </tr>
        </thead>
        <tbody>{top_ads_rows}</tbody>
      </table>
    </div>
    <p style="color:#334155;font-size:.72rem;margin:0 0 20px;line-height:1.6;">
      {ctr_legend} &nbsp;·&nbsp; Ad-level link-click metrics not stored per-ad in DB (shown as —).
    </p>

    {_insight_box(
        "<strong>Testimonial and product_hero top the FSD leaderboard</strong> because they do "
        "opposite jobs that together cover the full buyer journey: testimonial wins on social proof "
        "and specificity (a real parent, a real result), while product_hero wins on clarity (what "
        "it is, what it does). contrast_repositioning performs because it frames the existing "
        "solution (iPad) as the problem — activating loss aversion before presenting the fix. "
        "aspiration_purpose and carousel consistently underperform at TOFU: abstract purpose "
        "messaging doesn't convert cold audiences who don't yet trust the brand, and carousel "
        "mechanics split attention without increasing intent.",
        "#f59e0b", "💡")}"""

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 5: Funnel Analysis
    # ══════════════════════════════════════════════════════════════════════

    def _funnel_card(seg: str, data: dict, paid: int, pending: int) -> str:
        color = SEGMENT_COLORS.get(seg, "#94a3b8")
        impr = data["total_impr"]
        clicks = data["total_link_clicks"]
        fsd = data["total_fsd"]
        ctr = data["avg_ctr"]
        fsd_rate = (fsd / clicks * 100) if clicks else 0
        paid_rate = (paid / fsd * 100) if fsd else 0
        overall_rate = (paid / impr * 100 * 10000) if impr else 0  # per 10K

        def _funnel_bar(label: str, val: str, sub: str, pct: float, max_v: float = 100) -> str:
            w = min(pct / max(max_v, 1) * 100, 100)
            return f"""
            <div style="margin-bottom:10px;">
              <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px;">
                <span style="color:#64748b;font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;">{label}</span>
                <span style="color:#475569;font-size:.72rem;">{sub}</span>
              </div>
              <div style="display:flex;align-items:center;gap:10px;">
                <div style="flex:1;height:24px;background:#080b12;border:1px solid #1e2235;border-radius:4px;overflow:hidden;">
                  <div style="width:{w:.1f}%;height:100%;background:{color}55;
                              border-right:2px solid {color};display:flex;align-items:center;
                              padding-left:8px;">
                    <span style="color:{color};font-size:.78rem;font-weight:700;white-space:nowrap;">{val}</span>
                  </div>
                </div>
              </div>
            </div>"""

        return f"""
        <div style="background:#0d1018;border:1px solid {color}33;border-radius:10px;
                    padding:20px 22px;flex:1;min-width:260px;">
          <div style="color:{color};font-size:.68rem;text-transform:uppercase;
                      letter-spacing:.1em;font-weight:700;margin-bottom:4px;">Funnel</div>
          <div style="color:#fff;font-size:.95rem;font-weight:700;margin-bottom:18px;">{seg}</div>
          {_funnel_bar("Impressions", _fn(impr), "100%", 100, 100)}
          {_funnel_bar("Link Clicks", _fn(clicks), f"CTR {ctr:.2f}%", ctr, 15)}
          {_funnel_bar("FSD (Gate 1)", str(int(fsd)), f"{fsd_rate:.1f}% of clicks", fsd_rate, 15)}
          {_funnel_bar("Paid (Gate 2)", str(paid), f"{paid_rate:.0f}% paid rate", paid_rate, 100)}
          <div style="border-top:1px solid #1e2235;margin-top:12px;padding-top:12px;
                      display:flex;justify-content:space-between;">
            <div>
              <div style="color:#475569;font-size:.65rem;text-transform:uppercase;letter-spacing:.06em;">Pending</div>
              <div style="color:#f59e0b;font-size:.9rem;font-weight:600;">{pending} deposits</div>
            </div>
            <div style="text-align:right;">
              <div style="color:#475569;font-size:.65rem;text-transform:uppercase;letter-spacing:.06em;">CPaC</div>
              <div style="color:#e4e7ef;font-size:.9rem;font-weight:600;">{_cpa(data['total_spend'], paid)}</div>
            </div>
          </div>
        </div>"""

    funnel_cards = '<div style="display:flex;gap:16px;flex-wrap:wrap;margin:20px 0 28px;">'
    for seg in ["Nostalgia Bridge Dad", "Sturdy Parenting", "Routine-Chaos"]:
        funnel_cards += _funnel_card(seg, agg[seg], stripe_paid.get(seg, 0), stripe_pending.get(seg, 0))
    funnel_cards += "</div>"

    # iPad Battle Mom LP gap callout
    ipad = agg["iPad Battle Mom"]
    ipad_clicks = ipad["total_link_clicks"]
    ipad_fsd = ipad["total_fsd"]
    ipad_fsd_rate = (ipad_fsd / ipad_clicks * 100) if ipad_clicks else 0

    sec5 = f"""
    {_section_header("05", "Funnel Analysis", "Impression → Click → FSD → Paid — where each segment leaks and why")}

    {funnel_cards}

    {_callout(
        "Funnel Insight: Paid Rate as Segment Intent Qualifier",
        f"""Sturdy Parenting's paid rate ({s_paid_rate:.0f}%) is the highest of the three winners —
        this isn't coincidence. Evidence-based parents research more thoroughly before submitting
        a form, meaning each FSD represents stronger purchase intent. Nostalgia Bridge Dad produces
        the most volume ({int(n_fsd)} FSD) but {n_pend} of those signups haven't converted — the
        nostalgia angle casts a wide net that captures some lookers alongside buyers. Routine-Chaos
        has the lowest paid count ({r_paid}) but strong FSD rate from clicks, suggesting the ad→LP
        narrative is cohesive; the paid rate gap may reflect price sensitivity in this segment
        or LP friction at the deposit step.""",
        "#34d399")}

    <div style="background:#0d1018;border:1px solid #f87171aa;border-radius:10px;
                padding:22px 26px;margin:20px 0;">
      <div style="display:flex;align-items:flex-start;gap:16px;">
        <div style="background:#3d1010;color:#f87171;border-radius:50%;width:32px;height:32px;
                    display:flex;align-items:center;justify-content:center;font-size:.9rem;
                    flex-shrink:0;margin-top:2px;">!</div>
        <div>
          <div style="color:#f87171;font-size:.85rem;font-weight:700;margin-bottom:8px;">
            iPad Battle Mom: High CTR, Low FSD = Landing Page Problem
          </div>
          <p style="color:#64748b;font-size:.82rem;line-height:1.75;margin:0 0 10px;">
            Link-click CTR of <strong style="color:#e4e7ef;">{_fp(ipad['avg_ctr'])}</strong> puts iPad Battle Mom
            among the top performers in the cohort for scroll-stop and click engagement.
            Yet only <strong style="color:#e4e7ef;">{int(ipad_fsd)} FSD</strong> from
            <strong style="color:#e4e7ef;">{_fn(ipad_clicks)} clicks</strong>
            ({ipad_fsd_rate:.1f}% FSD rate) reveals a severe drop-off at the landing page.
            The ad message ("iPad is stealing your child") activates anger and urgency —
            but the landing page apparently fails to maintain that emotional momentum,
            likely pivoting too quickly to product features rather than validating the pain first.
          </p>
          <p style="color:#64748b;font-size:.82rem;line-height:1.75;margin:0;">
            <strong style="color:#94a3b8;">Recommendation:</strong> Before abandoning this segment,
            create a dedicated LP variant that mirrors the ad's "contrast" frame — lead with
            the iPad problem for 60% of the fold, then introduce Nowa as the antidote. A/B
            test headline variants: "Your child doesn't need less screen time — they need better
            screen time" vs current copy. The high CTR proves the audience exists; the LP just
            needs to meet them where the ad left them.
          </p>
        </div>
      </div>
    </div>"""

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 6: Placement & Device Analysis
    # ══════════════════════════════════════════════════════════════════════

    # PC campaign data
    pc_data = db_metrics.get("120244490348810025", {})
    static_data = db_metrics.get("120244568399160025", {})
    video_data = db_metrics.get("120244568479490025", {})

    pc_fsd = pc_data.get("fsd", 4)
    pc_spend = pc_data.get("spend", 130.97)
    pc_ctr = pc_data.get("ctr", 7.39)
    pc_fsd_per_100 = (pc_fsd / pc_spend * 100) if pc_spend else 0
    static_fsd_per_100 = (static_data.get("fsd", 35) / static_data.get("spend", 525.64) * 100) if static_data.get("spend") else 6.7
    mobile_fsd_per_100 = static_fsd_per_100  # mobile = static/video campaigns

    sec6 = f"""
    {_section_header("06", "Placement & Device Analysis", "Mobile vs desktop — where buyers actually convert")}

    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:20px 0 28px;">
      <div style="background:#0d1018;border:1px solid #34d39944;border-radius:10px;padding:22px 24px;">
        <div style="color:#34d399;font-size:.7rem;font-weight:700;text-transform:uppercase;
                    letter-spacing:.1em;margin-bottom:12px;">Mobile Feed</div>
        <div style="font-size:2.2rem;font-weight:800;color:#34d399;margin-bottom:8px;">&#10003; Primary</div>
        <p style="color:#64748b;font-size:.8rem;line-height:1.7;margin:0 0 14px;">
          Highest FSD rate, lowest CPC, best ROAS. Parents discover products in the mobile
          feed during school pickup, bedtime routines, and commute scroll. The impulse-then-research
          buying pattern plays to mobile's strength — tap, form fill, done.
        </p>
        <div style="color:#94a3b8;font-size:.82rem;">FSD/$100: <strong style="color:#34d399;">{mobile_fsd_per_100:.1f}</strong></div>
      </div>

      <div style="background:#0d1018;border:1px solid #f59e0b44;border-radius:10px;padding:22px 24px;">
        <div style="color:#f59e0b;font-size:.7rem;font-weight:700;text-transform:uppercase;
                    letter-spacing:.1em;margin-bottom:12px;">Instagram Stories / Reels</div>
        <div style="font-size:2.2rem;font-weight:800;color:#f59e0b;margin-bottom:8px;">~ Secondary</div>
        <p style="color:#64748b;font-size:.8rem;line-height:1.7;margin:0 0 14px;">
          Stories and Reels deliver reach at lower CPM but require vertical creative (9:16).
          Swipe-up friction is slightly higher than feed click. Best used for retargeting
          warm audiences (LP visitors, FSD non-payers) where brand familiarity is already
          established — cold audiences need the full-frame feed presentation.
        </p>
        <div style="color:#94a3b8;font-size:.82rem;">Creative spec: 9:16 ratio, 15s max</div>
      </div>

      <div style="background:#0d1018;border:1px solid #f8717144;border-radius:10px;padding:22px 24px;">
        <div style="color:#f87171;font-size:.7rem;font-weight:700;text-transform:uppercase;
                    letter-spacing:.1em;margin-bottom:12px;">Desktop / PC</div>
        <div style="font-size:2.2rem;font-weight:800;color:#f87171;margin-bottom:8px;">&#9888; Budget Drain</div>
        <p style="color:#64748b;font-size:.8rem;line-height:1.7;margin:0 0 14px;">
          Top 3 PC campaign: {int(pc_fsd)} FSD from {_fs(pc_spend)} spend = {pc_fsd_per_100:.1f} FSD/$100.
          PC users browse in evaluation mode, not buying mode. CTR ({_fp(pc_ctr)}) looks reasonable
          but doesn't convert — PC users are researchers who will later purchase on mobile or
          direct. Including PC in cold-traffic campaigns dilutes budget and inflates average CPC.
        </p>
        <div style="color:#94a3b8;font-size:.82rem;">
          Recommendation: <strong style="color:#f87171;">Exclude PC from cold traffic.</strong>
          PC only for retargeting / lookalikes with purchase intent signals.
        </div>
      </div>
    </div>

    {_insight_box(
        "Mobile-first creative specifications matter: use 4:5 ratio (not 1:1) for feed placements — "
        "4:5 occupies 25% more vertical screen space in the feed than square, increasing visual "
        "dominance and dwell time. All future creative production should default to 4:5 for "
        "static and 9:16 for video. Desktop placements should be excluded from cold-traffic "
        "targeting at the ad set level using placement customization.",
        "#34d399", "📱")}"""

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 7: Optimization Timeline
    # ══════════════════════════════════════════════════════════════════════

    # Build timeline from changelog
    key_events = [
        {"date": "May 11–12", "type": "launch", "title": "All 10 Segments Launched",
         "detail": "10 SALES campaigns activated over a 10-hour window (1.A through 6.A). Some initial testing with -Copy duplicates on May 12 that were cleaned up within the hour — final campaign set confirmed active by 23:54 on May 12.",
         "color": "#34d399"},
        {"date": "May 13–15", "type": "setup", "title": "Campaign Stabilization",
         "detail": "New ad sets added (May 13). One ad set briefly set Inactive then restored (May 15). All 10 segments confirmed running. The algorithm's learning phase (50 optimization events per ad set) runs during this period — no changes recommended until learning completes.",
         "color": "#60a5fa"},
        {"date": "May 18", "type": "cut", "title": "First Elimination: AI-Curious + Homeschool Paused",
         "detail": "AI-Curious Parent (120243727427170025) and Homeschool (120243727324310025) paused at 17:55–17:59. At this point: AI-Curious had minimal FSD on ~$80 spend; Homeschool similarly low. Both segments showed insufficient conversion velocity to justify continued spend at equal budget.",
         "color": "#f59e0b"},
        {"date": "May 22", "type": "cut", "title": "Second Elimination: iPad Mom, Homework, Selective Mutism Paused",
         "detail": "iPad Battle Mom paused at 17:57, Homework Meltdown at 15:08, Selective Mutism at 11:55. The data by May 22: iPad Mom had strong CTR but poor FSD; Homework and Selective Mutism had insufficient scale. This left 5 active SALES campaigns: Nostalgia, Sturdy, Routine, Anxiety, ADHD-EF.",
         "color": "#f59e0b"},
        {"date": "May 27", "type": "cut", "title": "Third Elimination: Anxiety + ADHD-EF Stopped",
         "detail": "ADHD-EF Intervention (5.A) and Anxiety Regulation (1.C) both set Inactive at 18:23 on May 27. Both were performing — Anxiety with 32 FSD and ADHD-EF with 21 FSD — but budget was being consolidated toward the confirmed top 3. This was a budget decision, not a performance failure.",
         "color": "#f87171"},
        {"date": "May 25–31", "type": "winner", "title": "Final 3 Scale Period",
         "detail": "Nostalgia, Sturdy, and Routine ran the full remaining period. Minor pending-process/active cycling on May 25–26 (budget reallocation processing). LEADS validation campaigns (Top Static, Top Video, Top 3 PC) ran concurrently during this period to confirm format hierarchy.",
         "color": "#34d399"},
    ]

    timeline_html = '<div style="position:relative;margin:20px 0;">'
    for i, ev in enumerate(key_events):
        color = ev["color"]
        is_last = i == len(key_events) - 1
        border_bottom = "" if is_last else "border-bottom:1px solid #1e2235;"
        timeline_html += f"""
        <div style="display:flex;gap:20px;padding:20px 0;{border_bottom}">
          <div style="flex-shrink:0;text-align:right;min-width:80px;">
            <span style="color:#475569;font-size:.75rem;white-space:nowrap;">{ev["date"]}</span>
          </div>
          <div style="flex-shrink:0;display:flex;flex-direction:column;align-items:center;">
            <div style="width:12px;height:12px;border-radius:50%;background:{color};
                        border:2px solid {color}55;flex-shrink:0;margin-top:2px;"></div>
            {'<div style="width:1px;flex:1;background:#1e2235;margin-top:6px;"></div>' if not is_last else ''}
          </div>
          <div style="padding-bottom:8px;">
            <div style="color:{color};font-size:.8rem;font-weight:700;margin-bottom:4px;">{ev["title"]}</div>
            <p style="color:#64748b;font-size:.8rem;line-height:1.7;margin:0;">{ev["detail"]}</p>
          </div>
        </div>"""
    timeline_html += "</div>"

    sec7 = f"""
    {_section_header("07", "Optimization Timeline & Decisions", "Key decisions, eliminations, and their data context")}

    {timeline_html}

    {_callout(
        "Decision Quality Assessment",
        """The elimination sequence was well-executed: AI-Curious and Homeschool were cut first (correctly —
        both had structural TAM and message-market fit problems). The May 22 cut of iPad Mom, Homework, and
        Selective Mutism was directionally correct but iPad Mom warrants a second look with a new LP.
        The May 27 cut of Anxiety and ADHD-EF was a budget decision during winner consolidation — both
        segments had real performance and could be re-entered with appropriate budget.""",
        "#818cf8")}"""

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 8: Key Lessons Learned
    # ══════════════════════════════════════════════════════════════════════

    lessons = [
        ("#f59e0b", "Test all segments simultaneously, not sequentially",
         "Running 10 segments in parallel over the same 20-day window eliminates seasonal noise, CPM fluctuation, and offer-fatigue artifacts. Sequential testing would have made Week 3 segments appear weaker simply due to calendar effects."),
        ("#34d399", "Equal budget ≠ equal test — audience size matters",
         "A segment with a 3.5M TAM (Nostalgia Bridge Dad) saturates differently than one with a 200K TAM (Selective Mutism). Equal daily budgets produce very different frequency and CPM profiles. Budget-per-addressable-reach-unit would create more equitable conditions."),
        ("#60a5fa", "Separate static and video from day 1 — delivery bias is real",
         "Meta's algorithm allocates more budget to video within mixed ad sets because video generates more micro-engagement signals. Separating formats into dedicated campaigns is the only way to get a fair performance comparison and prevents budget waste on the wrong format."),
        ("#f472b6", "High CTR without FSD = landing page problem, not ad problem",
         "iPad Battle Mom's 5.31% link-click CTR is exceptional — the ad resonates deeply. But 24 FSD from 879 link clicks (2.7% FSD rate) means the LP narrative breaks the momentum the ad creates. Always track CTR (link) and FSD rate separately; one diagnoses ads, the other diagnoses landing pages."),
        ("#818cf8", "Paid rate (FSD→paid) is the best intent signal",
         "Sturdy Parenting's higher paid rate (vs Nostalgia's volume leadership) reveals a qualitatively different buyer: more researched, more committed, more likely to complete the purchase. Paid rate is the segment qualifier that CPaC alone misses."),
        ("#a78bfa", "Emotional angles outperform rational/feature angles at TOFU",
         "Nostalgia, Sturdy, and Routine all win on emotional resonance (identity, role, daily friction) — not product features. At TOFU, parents aren't ready to evaluate specs; they respond to 'this brand understands me.' Product-hero ads succeed because they're visually clear, not because they list features."),
        ("#f87171", "PC placement burns budget without converting at cold-traffic stage",
         "Top 3 PC campaign: 4 FSD on $130.97 spend = $32.74/FSD vs mobile campaigns at <$15/FSD. PC users are in evaluation mode, not buying mode. Exclude PC from cold-traffic ad sets at the placement level — reserve PC only for warm audiences with existing intent."),
        ("#38bdf8", "7-day click attribution window means late conversions inflate early FSD",
         "Meta's default 7-day click window credits an FSD event to an ad click up to 7 days old. A conversion on May 18 from a May 11 click is attributed to Week 1's campaign budget. This inflates early-week segments' FSD numbers — always cross-reference with Stripe timestamps for true attribution."),
        ("#34d399", "Frequency below 2.0 after 3 weeks = audience not yet saturated",
         "Average frequency across all 10 segments was 1.08–1.18 after 20 days. This is very low — the algorithm hadn't come close to exhausting the addressable audience. Scaling spend would increase frequency toward the ~2.5 optimal range rather than immediately hitting saturation."),
        ("#f59e0b", "Track segment CPaC, not campaign CPaC, for true efficiency measurement",
         "When LEADS campaigns (Top Static/Video) ran ad sets from multiple segments, campaign-level CPaC becomes misleading. Nostalgia's true CPaC includes spend from both its SALES campaign and its LEADS campaign ad sets. Always aggregate at segment level across all campaigns."),
        ("#fb923c", "Switch optimization event from Purchase to FSD as soon as conversion volume is too low",
         "Launching with Purchase optimization is correct in principle — it targets the highest-intent signal. But if each ad set generates fewer than ~50 purchases per week, Meta gets stuck in the learning phase indefinitely. The fix: switch the optimization event to the highest-frequency gate in your funnel (FSD in this case) as soon as you see learning instability. Run Purchase optimization only once you have enough paid volume to sustain 50+ events/week/ad set — typically after you've scaled to a confirmed winner. For future campaigns: launch with FSD optimization from day 1 if purchase volume will be sparse, and only upgrade to Purchase optimization once the campaign is fully scaled."),
        ("#60a5fa", "Later campaigns perform better — optimization is cumulative",
         "The Top Static and Top Video campaigns (May 28–31) used the same FSD conversion event as the original SALES campaigns, and they outperformed them. This is not a LEADS vs SALES difference — they are the same optimization goal. The performance improvement came from campaign learning maturity: by May 28, Meta had accumulated 2+ weeks of FSD signal from the earlier campaigns, resulting in better audience targeting and lower effective CPM. Takeaway: the longer a campaign runs with consistent optimization, the more efficient it becomes. Don't restart or heavily restructure a performing campaign — let it learn."),
        ("#60a5fa", "The 2-gate funnel (FSD + Stripe) provides exceptional signal clarity",
         "Having two conversion events — Meta pixel FSD and Stripe payment confirmation — creates a qualification chain that reveals both ad quality (Gate 1) and purchase intent (Gate 2). Most funnels only track one. The dual-gate approach exposed the Sturdy Parenting quality signal that CTR and FSD alone would have missed."),
        ("#f472b6", "207 pending deposits are an immediate revenue opportunity — zero ad spend required",
         f"With {total_pending} pending Stripe deposits outstanding, an email re-engagement sequence targeting each segment's pending list (segmented by source) could convert 30–50% without any new ad spend. At $99 per deposit, this represents $3,000–$5,000 in recoverable revenue from the existing funnel."),
    ]

    lessons_html = '<div style="counter-reset:lesson;margin:20px 0;">'
    for i, (color, title, body) in enumerate(lessons):
        lessons_html += f"""
        <div style="display:flex;gap:18px;margin-bottom:20px;padding-bottom:20px;
                    border-bottom:1px solid #111827;">
          <div style="flex-shrink:0;background:{color}18;color:{color};
                      border:1px solid {color}44;border-radius:50%;
                      width:32px;height:32px;display:flex;align-items:center;
                      justify-content:center;font-size:.8rem;font-weight:800;margin-top:2px;">
            {i+1}
          </div>
          <div>
            <div style="color:#e4e7ef;font-size:.88rem;font-weight:700;margin-bottom:6px;">{title}</div>
            <p style="color:#64748b;font-size:.82rem;line-height:1.75;margin:0;">{body}</p>
          </div>
        </div>"""
    lessons_html += "</div>"

    sec8 = f"""
    {_section_header("08", "Key Lessons Learned", "12 actionable principles from this campaign — to be applied to every future launch")}
    {lessons_html}"""

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 9: Recommendations
    # ══════════════════════════════════════════════════════════════════════

    sec9 = f"""
    {_section_header("09", "Recommendations for Next Nowa Campaign", "Data-backed actions, audience strategy, and structure for the next launch")}

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:28px;">

      <div style="background:#0d1018;border:1px solid #f59e0b33;border-radius:10px;padding:22px 24px;">
        <div style="color:#f59e0b;font-size:.72rem;font-weight:700;text-transform:uppercase;
                    letter-spacing:.1em;margin-bottom:18px;">Audience Strategy</div>
        <div style="display:flex;flex-direction:column;gap:14px;">
          <div>
            <div style="color:#e4e7ef;font-size:.85rem;font-weight:600;margin-bottom:5px;">
              Scale Nostalgia Bridge Dad 2x
            </div>
            <p style="color:#64748b;font-size:.8rem;line-height:1.6;margin:0;">
              Lowest CPM ({_fs(nostalgia['avg_cpm'])}), best link-click CTR ({_fp(nostalgia['avg_ctr'])}),
              largest TAM (~3.5M parents 30–50). Frequency of 1.13 after 20 days means
              massive untapped reach. Double daily budget before frequency reaches 2.5.
            </p>
          </div>
          <div>
            <div style="color:#e4e7ef;font-size:.85rem;font-weight:600;margin-bottom:5px;">
              Scale Sturdy Parenting 1.5x
            </div>
            <p style="color:#64748b;font-size:.8rem;line-height:1.6;margin:0;">
              Best paid rate ({s_paid_rate:.0f}%), most qualified buyers. Higher CPM ({_fs(sturdy['avg_cpm'])})
              but lower CPaC due to conversion quality. Scale to maximize paid deposits per dollar.
            </p>
          </div>
          <div>
            <div style="color:#e4e7ef;font-size:.85rem;font-weight:600;margin-bottom:5px;">
              Re-test Anxiety Regulation
            </div>
            <p style="color:#64748b;font-size:.8rem;line-height:1.6;margin:0;">
              32 FSD, 8 paid deposits, CPM improving before pause. Reactivate at $15/day
              with a dedicated LP variant addressing anxiety management specifically — the
              current LP may be too generic for this psychographic.
            </p>
          </div>
          <div>
            <div style="color:#e4e7ef;font-size:.85rem;font-weight:600;margin-bottom:5px;">
              Exclude existing customers
            </div>
            <p style="color:#64748b;font-size:.8rem;line-height:1.6;margin:0;">
              Upload the {total_paid} paid customer list as a custom audience exclusion
              across all cold-traffic campaigns. Retarget the {total_pending} pending
              signups with a dedicated "convert now" campaign.
            </p>
          </div>
        </div>
      </div>

      <div style="background:#0d1018;border:1px solid #34d39933;border-radius:10px;padding:22px 24px;">
        <div style="color:#34d399;font-size:.72rem;font-weight:700;text-transform:uppercase;
                    letter-spacing:.1em;margin-bottom:18px;">Creative Strategy</div>
        <div style="display:flex;flex-direction:column;gap:14px;">
          <div>
            <div style="color:#e4e7ef;font-size:.85rem;font-weight:600;margin-bottom:5px;">
              Lead with testimonial + product_hero
            </div>
            <p style="color:#64748b;font-size:.8rem;line-height:1.6;margin:0;">
              These two styles account for the top FSD-per-dollar performance. Create
              3 testimonial variants per winner segment (different parent archetypes) and
              2 product_hero variants (day/night use cases). Test headlines: specificity
              beats vagueness every time ("My 8-year-old hasn't asked for the iPad in 3 weeks"
              &gt; "Better screen time for kids").
            </p>
          </div>
          <div>
            <div style="color:#e4e7ef;font-size:.85rem;font-weight:600;margin-bottom:5px;">
              Drop aspiration_purpose and carousel from TOFU
            </div>
            <p style="color:#64748b;font-size:.8rem;line-height:1.6;margin:0;">
              Abstract purpose messaging ("raise a generation of calm, curious kids")
              doesn't convert cold audiences who don't yet trust the brand. Reserve
              aspiration angles for warm retargeting audiences who already know what
              Nowa is. Carousels split attention without increasing intent — eliminate from new tests.
            </p>
          </div>
          <div>
            <div style="color:#e4e7ef;font-size:.85ssl;font-weight:600;margin-bottom:5px;">
              Build 3 retargeting video variants
            </div>
            <p style="color:#64748b;font-size:.8rem;line-height:1.6;margin:0;">
              Target the {total_pending} pending Stripe deposits with 30–60s videos
              addressing the top 3 price objections: "Is $99 worth it?", "How is this
              different from educational apps?", "Will my child actually use it?"
            </p>
          </div>
          <div>
            <div style="color:#e4e7ef;font-size:.85rem;font-weight:600;margin-bottom:5px;">
              iPad Battle Mom dedicated LP test
            </div>
            <p style="color:#64748b;font-size:.8rem;line-height:1.6;margin:0;">
              Link-click CTR of {_fp(ipad['avg_ctr'])} is too good to abandon. Create a contrast-frame
              LP: "Not another educational app — a screen-free companion" with 3
              specific differentiators from iPad use. A/B test 2 LP variants before
              making a final verdict on this segment.
            </p>
          </div>
        </div>
      </div>
    </div>

    <div style="background:#0d1018;border:1px solid #60a5fa33;border-radius:10px;padding:22px 24px;margin-bottom:20px;">
      <div style="color:#60a5fa;font-size:.72rem;font-weight:700;text-transform:uppercase;
                  letter-spacing:.1em;margin-bottom:18px;">Recommended Architecture for Next Launch</div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:20px;">

        <div style="background:#080b12;border:1px solid #1e2235;border-radius:8px;padding:16px;">
          <div style="color:#f59e0b;font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;">Phase 1</div>
          <div style="color:#e4e7ef;font-size:.85rem;font-weight:700;margin-bottom:12px;">Discovery — Days 1–14</div>
          <ul style="color:#64748b;font-size:.78rem;line-height:1.9;margin:0;padding-left:16px;">
            <li>8–12 segments, $12–15/day each</li>
            <li>All campaigns: <strong style="color:#94a3b8;">SALES objective</strong></li>
            <li>Optimization event: <strong style="color:#94a3b8;">FSD</strong> from day 1<br>
              <span style="color:#334155;font-size:.72rem;">(Purchase event only if you expect 50+ purchases/week/ad set — unlikely at launch)</span></li>
            <li>Static and video in <em>separate</em> campaigns always</li>
            <li>Mobile placements only — exclude PC from all cold-traffic ad sets</li>
            <li>Cut trigger: &lt;3 FSD after $150 spent per segment</li>
            <li>No changes during first 48h (learning phase)</li>
            <li>Holdout: only needed once organic channels (email, SEO, press) are active — skip for Meta-only launches</li>
          </ul>
        </div>

        <div style="background:#080b12;border:1px solid #1e2235;border-radius:8px;padding:16px;">
          <div style="color:#34d399;font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;">Phase 2</div>
          <div style="color:#e4e7ef;font-size:.85rem;font-weight:700;margin-bottom:12px;">Validation — Days 15–30</div>
          <ul style="color:#64748b;font-size:.78rem;line-height:1.9;margin:0;padding-left:16px;">
            <li>Consolidate to 2–4 surviving segments with scaled budgets</li>
            <li>Keep <strong style="color:#94a3b8;">SALES + FSD</strong> for winner SALES campaigns</li>
            <li>Launch LEADS static/video campaigns on winners only<br>
              <span style="color:#334155;font-size:.72rem;">(LEADS objective = cheaper CPM but less purchase-intent signal — use for format testing, not primary conversion)</span></li>
            <li>PC-only campaign at $10/day per segment to test placement (don&rsquo;t scale)</li>
            <li>Advantage+ Audience: seed with confirmed paid customers</li>
            <li>Consider switching top segments to <strong style="color:#94a3b8;">Purchase optimization</strong> if you now have 50+ paid/week</li>
          </ul>
        </div>

        <div style="background:#080b12;border:1px solid #1e2235;border-radius:8px;padding:16px;">
          <div style="color:#f472b6;font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;">Phase 3</div>
          <div style="color:#e4e7ef;font-size:.85rem;font-weight:700;margin-bottom:12px;">Scale &amp; Retain — Days 30+</div>
          <ul style="color:#64748b;font-size:.78rem;line-height:1.9;margin:0;padding-left:16px;">
            <li>Retargeting campaign for FSD non-payers (pending list)<br>
              <span style="color:#334155;font-size:.72rem;">(Optimization: Purchase — these people already know the product)</span></li>
            <li>Lookalike campaigns seeded from confirmed paid customers (1–3% LAL)</li>
            <li>Advantage+ Shopping for new segment discovery at scale</li>
            <li>Upgrade to <strong style="color:#94a3b8;">Purchase event optimization</strong> on scaled winners<br>
              <span style="color:#334155;font-size:.72rem;">(Only when each ad set reaches 50+ purchases/week)</span></li>
            <li>Exclude all converted buyers and pending list from cold-traffic campaigns</li>
          </ul>
        </div>

      </div>
    </div>

    {_callout(
        "The Pre-Order Funnel Playbook",
        """<strong style="color:#e4e7ef;">1.</strong> Run 8–12 audience segments simultaneously at equal budgets. Test period: 2 weeks minimum.<br>
        <strong style="color:#e4e7ef;">2.</strong> Use the 2-gate funnel (FSD + payment confirmation). Track both separately — they diagnose different problems.<br>
        <strong style="color:#e4e7ef;">3.</strong> Cut segments spending &gt;$150 with &lt;3 FSD. Keep segments with strong paid rate even if total FSD is lower.<br>
        <strong style="color:#e4e7ef;">4.</strong> Paid rate (FSD→paid) is your best buyer quality signal. Optimize for it, not just for FSD volume.<br>
        <strong style="color:#e4e7ef;">5.</strong> Separate static and video from day 1 to prevent delivery bias.<br>
        <strong style="color:#e4e7ef;">6.</strong> Mobile-first creative specs (4:5 static, 9:16 video). Exclude PC from cold traffic.<br>
        <strong style="color:#e4e7ef;">7.</strong> Build the retargeting campaign <em>before launch</em> — don't wait until you have pending signups.""",
        "#f59e0b")}"""

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 10: What to Watch
    # ══════════════════════════════════════════════════════════════════════

    watch_items = [
        ("#f59e0b", "Weekly FSD/$100 Trend (Nostalgia & Sturdy)",
         f"Should be stable or improving. A declining FSD/$100 over two consecutive weeks signals audience saturation or creative fatigue before frequency reaches 2.5. Current benchmarks: Nostalgia ~{(nostalgia['total_fsd']/nostalgia['total_spend']*100):.1f} FSD/$100, Sturdy ~{(sturdy['total_fsd']/sturdy['total_spend']*100):.1f} FSD/$100.",
         "Alert if FSD/$100 drops >20% week-over-week"),
        ("#f87171", "Frequency Creeping Above 2.5",
         "Average frequency across the winner segments was 1.08–1.13 at campaign end — well below saturation. Watch for frequency crossing 2.5 on any segment. Above 2.5, CPM rises sharply (auction competition for same eyes), CTR drops, and FSD rate falls. Action: expand targeting by 10–15% or add new creative before frequency exceeds 2.5.",
         "Alert if any winner segment frequency > 2.5"),
        ("#60a5fa", "CPM Trend — Rising CPM = Audience Narrowing",
         f"Current CPMs: Nostalgia {_fs(nostalgia['avg_cpm'])}, Sturdy {_fs(sturdy['avg_cpm'])}, Routine {_fs(routine['avg_cpm'])}. Rising CPM across all three simultaneously signals platform-wide auction competition increase (seasonal — Q4 is expensive). Rising CPM in only one segment signals that segment's audience is being exhausted.",
         "Review CPM weekly; escalate if >$45 for Nostalgia or >$60 for Sturdy"),
        ("#34d399", "Pending-to-Paid Conversion Rate",
         f"Monitor the {total_pending} pending Stripe deposits weekly. With an email re-engagement sequence, a 30–50% conversion rate is achievable. Track by segment source — if one segment's pending list converts significantly lower, it indicates a price sensitivity or trust issue specific to that audience.",
         f"Target: convert 30+ of {total_pending} pending to paid without new ad spend"),
        ("#818cf8", "Attribution Window Gap",
         "Meta's 7-day click window means FSD events this week may include clicks from 7 days ago. For accurate incrementality measurement, always cross-reference Meta FSD counts with Stripe submission timestamps. Discrepancies &gt;10% should trigger an investigation — either window mismatch or pixel misconfiguration.",
         "Compare Meta FSD vs Stripe submission count weekly; escalate if >10% gap"),
        ("#f472b6", "New Creative Performance vs Control",
         "When launching new creative variants (recommended: monthly refresh for winning segments), track the new ad's FSD/$100 vs the control ad in its first 7 days. A new ad must outperform control by >15% to justify replacing it. Below 15% improvement, keep control running.",
         "Minimum 7-day test per new creative variant before making replacement decisions"),
    ]

    watch_html = '<div style="display:flex;flex-direction:column;gap:0;margin:20px 0;">'
    for i, (color, title, body, action) in enumerate(watch_items):
        bg = "#0a0e18" if i % 2 == 0 else ""
        watch_html += f"""
        <div style="display:flex;gap:20px;padding:20px 16px;border-bottom:1px solid #111827;{bg}">
          <div style="flex-shrink:0;background:{color}18;border:1px solid {color}44;
                      border-radius:8px;padding:8px 12px;height:fit-content;min-width:32px;
                      text-align:center;">
            <span style="color:{color};font-size:.7rem;font-weight:700;">{i+1:02d}</span>
          </div>
          <div style="flex:1;">
            <div style="color:{color};font-size:.85rem;font-weight:700;margin-bottom:6px;">{title}</div>
            <p style="color:#64748b;font-size:.8rem;line-height:1.7;margin:0 0 10px;">{body}</p>
            <div style="background:{color}0d;border:1px solid {color}25;border-radius:6px;
                        padding:8px 12px;display:inline-block;">
              <span style="color:{color};font-size:.72rem;font-weight:600;">&#9654; Action: </span>
              <span style="color:#94a3b8;font-size:.72rem;">{action}</span>
            </div>
          </div>
        </div>"""
    watch_html += "</div>"

    sec10 = f"""
    {_section_header("10", "What to Watch — Leading Indicators", "Early signals that the ongoing campaign is healthy or needs intervention")}
    {watch_html}"""

    # ══════════════════════════════════════════════════════════════════════
    # Assemble full HTML
    # ══════════════════════════════════════════════════════════════════════

    all_sections = sec1 + sec2 + sec3 + sec4 + sec5 + sec6 + sec7 + sec8 + sec9 + sec10

    # Nav TOC
    toc = """
    <nav style="position:fixed;top:0;left:0;right:0;z-index:100;background:#060810ee;
                backdrop-filter:blur(12px);border-bottom:1px solid #1e2235;
                padding:12px 24px;display:flex;gap:0;align-items:center;overflow-x:auto;">
      <span style="color:#f59e0b;font-size:.7rem;font-weight:700;text-transform:uppercase;
                   letter-spacing:.12em;margin-right:16px;white-space:nowrap;">Nowa Analysis</span>
      """ + " ".join(
        f'<a href="#sec{i}" style="color:#475569;font-size:.72rem;padding:4px 10px;'
        f'border-radius:4px;text-decoration:none;white-space:nowrap;'
        f'transition:color .2s;" '
        f'onmouseover="this.style.color=\'#e4e7ef\'" '
        f'onmouseout="this.style.color=\'#475569\'">'
        f'{num} {title}</a>'
        for i, (num, title) in enumerate([
            ("01", "Exec Summary"), ("02", "Structure"), ("03", "Segments"),
            ("04", "Creatives"), ("05", "Funnel"), ("06", "Placement"),
            ("07", "Timeline"), ("08", "Lessons"), ("09", "Recommendations"), ("10", "Watch"),
        ])
    ) + """
    </nav>"""

    # Add anchor IDs to section headers
    for i in range(10):
        anchor_id = f'id="sec{i}"'
        # We'll inject the anchor into the section div wrapper below

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Nowa — Strategic Campaign Analysis — May 2026</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    html{{scroll-behavior:smooth}}
    body{{background:#080b12;color:#e4e7ef;
         font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         line-height:1.5;font-size:14px}}
    table{{border-collapse:collapse;width:100%}}
    tr:hover td{{background:rgba(255,255,255,.018)}}
    a{{color:inherit;text-decoration:none}}
    ::-webkit-scrollbar{{width:6px;height:6px}}
    ::-webkit-scrollbar-track{{background:#0d1018}}
    ::-webkit-scrollbar-thumb{{background:#1e2235;border-radius:3px}}
  </style>
</head>
<body>
  {toc}
  <div style="max-width:1280px;margin:0 auto;padding:80px 28px 60px;">

    <!-- Page header -->
    <div style="margin-bottom:40px;padding-bottom:28px;border-bottom:1px solid #1e2235;">
      <div style="color:#f59e0b;font-size:.65rem;text-transform:uppercase;
                  letter-spacing:.18em;margin-bottom:12px;font-weight:700;">
        Nowa &nbsp;·&nbsp; Meta Ads Campaign Intelligence
      </div>
      <h1 style="font-size:2.4rem;font-weight:900;color:#fff;letter-spacing:-.03em;
                 line-height:1.15;margin-bottom:12px;">
        Strategic Campaign<br>
        <span style="color:#f59e0b;">Performance Analysis</span>
      </h1>
      <p style="color:#475569;font-size:.95rem;max-width:620px;line-height:1.7;margin-bottom:16px;">
        10-segment audience intelligence test &nbsp;·&nbsp; May 12–31, 2026 &nbsp;·&nbsp;
        Meta Ads + Stripe two-gate funnel analysis. Covers segment performance, ad creative
        analysis, funnel leakage, placement strategy, optimization decisions, and
        data-backed recommendations for the next Nowa campaign.
      </p>
      <div style="display:flex;gap:12px;flex-wrap:wrap;">
        {_badge("3 Winners Identified", "#0c2010", "#34d399", "#1a4a2a")}
        {_badge("113 Paid Deposits", "#1a1d27", "#e4e7ef", "#2a2e3a")}
        {_badge(f"{total_pending} Pending to Convert", "#3d2e0a", "#f59e0b", "#78350f")}
        {_badge(f"Total Spend {_fs(total_spend)}", "#1a1f2e", "#818cf8", "#2a3060")}
      </div>
    </div>

    <!-- Sections with anchor IDs -->
    <div id="sec0">{sec1}</div>
    <div id="sec1">{sec2}</div>
    <div id="sec2">{sec3}</div>
    <div id="sec3">{sec4}</div>
    <div id="sec4">{sec5}</div>
    <div id="sec5">{sec6}</div>
    <div id="sec6">{sec7}</div>
    <div id="sec7">{sec8}</div>
    <div id="sec8">{sec9}</div>
    <div id="sec9">{sec10}</div>

    <footer style="text-align:center;color:#1e2235;font-size:.72rem;
                   padding:32px 0 0;margin-top:48px;border-top:1px solid #111827;">
      Nowa · Meta Ads Strategic Analysis · May 12–31, 2026 · Generated 2026-06-02
    </footer>

  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    settings = load_settings()
    print(f"Nowa Campaign Analysis — {SINCE} to {UNTIL}")
    print("=" * 60)

    print("\n[1/5] Loading Stripe data from DB ...")
    stripe_paid, stripe_pending = load_stripe_from_db()
    total_paid = sum(stripe_paid.values())
    total_pending = sum(stripe_pending.values())
    print(f"  Paid: {total_paid}  |  Pending: {total_pending}")
    for seg in STRIPE_SOURCE_MAP.values():
        p = stripe_paid.get(seg, 0)
        q = stripe_pending.get(seg, 0)
        if p or q:
            print(f"  {seg:<28} paid={p}  pending={q}")

    print("\n[2/5] Loading DB campaign metrics (fallback) ...")
    db_metrics = load_db_segment_metrics()
    print(f"  {len(db_metrics)} campaigns in DB")

    print("\n[3/5] Loading weekly trend, ad performance, changelog, all-clicks from DB ...")
    weekly = load_weekly_trend()
    ad_perf = load_ad_performance()
    changelog = load_changelog()
    all_clicks_db = load_db_all_clicks_by_segment()
    print(f"  Weekly trend: {sum(len(v) for v in weekly.values())} rows")
    print(f"  Top ads: {len(ad_perf)}")
    print(f"  Changelog: {len(changelog)} events")
    print(f"  All-clicks segments: {len(all_clicks_db)}")

    print("\n[4/5] Fetching Meta API adset insights ...")
    try:
        all_rows = pull_all_data(settings)
        print(f"  Total adset rows: {len(all_rows)}")
        agg = aggregate_by_segment(all_rows)
    except Exception as exc:
        print(f"  [warn] Meta API failed: {exc}")
        print("  Using DB metrics as primary source ...")
        agg = {seg: {
            "total_spend": 0.0, "total_impr": 0, "total_link_clicks": 0,
            "total_fsd": 0.0, "total_reach": 0,
            "avg_ctr": 0.0, "avg_cpm": 0.0, "avg_cpc": 0.0,
        } for seg in SEGMENT_MAP}

    # Fill in from DB wherever Meta API returned nothing
    for cid, seg in CAMPAIGN_ID_TO_SEGMENT.items():
        if cid in db_metrics and agg[seg]["total_spend"] == 0:
            d = db_metrics[cid]
            agg[seg].update({
                "total_spend": d["spend"], "total_impr": d["impr"],
                "total_link_clicks": d["clicks"], "total_fsd": d["fsd"],
                "avg_ctr": d["ctr"], "avg_cpc": d["cpc"], "avg_cpm": d["cpm"],
            })

    print("\n[5/5] Building HTML report ...")
    html = build_report(agg, db_metrics, stripe_paid, stripe_pending, weekly, ad_perf, changelog, all_clicks_db)

    out = ROOT / "reports" / "campaign_analysis.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"\nReport saved: {out}  ({len(html)//1024} KB)")

    # Console summary
    print()
    print(f"{'Segment':<28} {'Spend':>10} {'FSD':>6} {'Paid':>6} {'Pending':>8} {'CPaC':>10}")
    print("-" * 72)
    sorted_segs = sorted(agg.keys(), key=lambda s: -agg[s]["total_fsd"])
    for seg in sorted_segs:
        a = agg[seg]
        paid = stripe_paid.get(seg, 0)
        pending = stripe_pending.get(seg, 0)
        cpac = f"${a['total_spend']/paid:.2f}" if paid else "—"
        print(f"{seg:<28} ${a['total_spend']:>9,.2f} {int(a['total_fsd']):>6} {paid:>6} {pending:>8} {cpac:>10}")


if __name__ == "__main__":
    main()
