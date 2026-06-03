"""Segment-based HTML performance report for Nowa Meta Ads campaigns.

Each ad set is attributed to an audience segment by name prefix/keyword,
regardless of which campaign it belongs to.

Run:
    python -X utf8 scripts/segment_report.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_settings  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SINCE = "2026-05-11"
UNTIL = "2026-05-31"

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

# Segment map: segment name → list of prefix/keyword matchers (checked in order)
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

# Stripe source → segment name
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
    "Homework Meltdown":    "#60a5fa",
    "Anxiety Regulation":   "#818cf8",
    "ADHD-EF Intervention": "#a78bfa",
    "iPad Battle Mom":      "#94a3b8",
    "Selective Mutism":     "#64748b",
    "AI-Curious Parent":    "#38bdf8",
    "Homeschool":           "#34d399",
}

DB_PATH = ROOT / "data" / "metrics.db"


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
    """Extract form_submit_deposit value from Meta conversions list."""
    if not conversions:
        return 0.0
    for item in conversions:
        at = item.get("action_type", "")
        if "form_submit_deposit" in at:
            return float(item.get("value", 0) or 0)
    return 0.0


def fetch_adset_names(campaign_id: str) -> dict[str, str]:
    """Return {adset_id: adset_name} for a campaign via the Ad Sets edge."""
    from facebook_business.adobjects.campaign import Campaign
    try:
        camp = Campaign(campaign_id)
        cursor = camp.get_ad_sets(fields=["id", "name"], params={"limit": 200})
        return {str(row["id"]): str(row["name"]) for row in _paginate(cursor)}
    except Exception as exc:
        print(f"    [warn] adset name fetch failed for {campaign_id}: {exc}")
        return {}


def fetch_campaign_adset_insights(campaign_id: str, campaign_label: str) -> list[dict]:
    """Pull adset-level insights for a campaign over the report date range."""
    from facebook_business.adobjects.adaccount import AdAccount
    from src.config import load_settings
    settings = load_settings()
    account = AdAccount(f"act_{settings.meta_ad_account_id.removeprefix('act_')}")

    fields = [
        "adset_id",
        "adset_name",
        "spend",
        "impressions",
        "inline_link_clicks",
        "inline_link_click_ctr",
        "cpm",
        "reach",
        "frequency",
        "conversions",
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
        print(f"    [warn] insights fetch failed: {exc}")
        return []

    rows = []
    for r in raw:
        fsd = _extract_fsd(r.get("conversions"))
        rows.append({
            "campaign_id":   campaign_id,
            "campaign_name": campaign_label,
            "adset_id":      str(r.get("adset_id", "")),
            "adset_name":    str(r.get("adset_name", "")),
            "spend":         float(r.get("spend", 0) or 0),
            "impressions":   int(r.get("impressions", 0) or 0),
            "link_clicks":   int(r.get("inline_link_clicks", 0) or 0),
            "reach":         int(r.get("reach", 0) or 0),
            "fsd":           fsd,
        })
    return rows


# ---------------------------------------------------------------------------
# 2. Segmentation logic
# ---------------------------------------------------------------------------

def classify_segment(adset_name: str) -> str | None:
    """Return the segment name for an ad set based on its name."""
    name_upper = adset_name.upper()
    name_lower = adset_name.lower()
    for segment, keywords in SEGMENT_MAP.items():
        for kw in keywords:
            if kw.upper() in name_upper:
                return segment
    # No match
    return None


# ---------------------------------------------------------------------------
# 3. Stripe data from DB
# ---------------------------------------------------------------------------

def load_stripe_by_segment() -> dict[str, int]:
    """Return {segment_name: paid_count} from DB stripe_payments table."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    result: dict[str, int] = {seg: 0 for seg in SEGMENT_MAP}
    for source, segment in STRIPE_SOURCE_MAP.items():
        cur.execute(
            "SELECT COUNT(*) FROM stripe_payments WHERE source=? AND status='paid'",
            (source,),
        )
        result[segment] = cur.fetchone()[0]
    conn.close()
    return result


# ---------------------------------------------------------------------------
# 4. Data aggregation
# ---------------------------------------------------------------------------

def pull_all_data(settings) -> tuple[list[dict], dict[str, str]]:
    """
    Pull insights for all campaigns at adset level.
    Returns (all_adset_rows, adset_id_to_name_map).
    """
    _init_api(settings)

    all_rows: list[dict] = []
    adset_names: dict[str, str] = {}

    for cid, clabel in ALL_CAMPAIGNS.items():
        print(f"  Fetching: {clabel} ({cid})")

        # Fetch adset names for this campaign
        names = fetch_adset_names(cid)
        adset_names.update(names)
        print(f"    {len(names)} ad sets found")

        # Fetch insights
        rows = fetch_campaign_adset_insights(cid, clabel)
        print(f"    {len(rows)} adset insight rows")

        # Fill in adset_name from names map if API returned it blank
        for r in rows:
            if not r["adset_name"] and r["adset_id"] in adset_names:
                r["adset_name"] = adset_names[r["adset_id"]]

        all_rows.extend(rows)

    return all_rows, adset_names


def aggregate_by_segment(
    all_rows: list[dict],
) -> dict[str, dict]:
    """
    Classify each adset row to a segment and aggregate totals.
    Returns {segment_name: agg_dict}.
    """
    # Initialize all segments
    agg: dict[str, dict] = {}
    for seg in SEGMENT_MAP:
        agg[seg] = {
            "total_spend":       0.0,
            "total_impr":        0,
            "total_link_clicks": 0,
            "total_fsd":         0.0,
            "total_reach":       0,
            "campaigns":         {},   # campaign_name → spend
            "adsets":            [],   # list of adset row dicts (for cross-campaign table)
        }

    unclassified: list[dict] = []

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

        cname = row["campaign_name"]
        a["campaigns"][cname] = a["campaigns"].get(cname, 0.0) + row["spend"]
        a["adsets"].append(row)

    if unclassified:
        print(f"\n  [info] {len(unclassified)} adset rows unclassified:")
        for r in unclassified:
            print(f"    campaign={r['campaign_name']}  adset='{r['adset_name']}'  spend={r['spend']:.2f}")

    # Compute derived metrics
    for seg, a in agg.items():
        clicks = a["total_link_clicks"] or 1
        impr   = a["total_impr"] or 1
        spend  = a["total_spend"]
        a["avg_ctr"] = (a["total_link_clicks"] / impr * 100) if a["total_impr"] else 0.0
        a["avg_cpc"] = (spend / clicks) if a["total_link_clicks"] else 0.0
        a["avg_cpm"] = (spend / impr * 1000) if a["total_impr"] else 0.0

    return agg


# ---------------------------------------------------------------------------
# 5. Cross-campaign ad sets detector
# ---------------------------------------------------------------------------

SEGMENT_HOME_CAMPAIGN_KEYWORDS: dict[str, str] = {
    "Nostalgia Bridge Dad": "Nostalgia",
    "Sturdy Parenting":     "Sturdy",
    "Routine-Chaos":        "Routine",
    "Homework Meltdown":    "Homework",
    "Anxiety Regulation":   "Anxiety",
    "ADHD-EF Intervention": "ADHD",
    "iPad Battle Mom":      "iPad",
    "Selective Mutism":     "Selective",
    "AI-Curious Parent":    "AI-Curious",
    "Homeschool":           "Homeschool",
}


def find_cross_campaign_adsets(all_rows: list[dict]) -> list[dict]:
    """
    Find ad sets whose segment doesn't match their campaign's 'home' segment.
    Only flag SALES campaigns (skip LEADS which are intentionally cross-segment).
    """
    cross: list[dict] = []
    seen: set[str] = set()  # deduplicate by adset_id

    for row in all_rows:
        # Only flag SALES campaigns for "unexpected" cross-placements
        if "LEADS" in row["campaign_name"]:
            continue

        seg = classify_segment(row["adset_name"])
        if seg is None:
            continue

        # Find the "home" keyword for this segment in the campaign name
        home_kw = SEGMENT_HOME_CAMPAIGN_KEYWORDS.get(seg, "")
        campaign_upper = row["campaign_name"].upper()

        if home_kw and home_kw.upper() not in campaign_upper:
            key = row["adset_id"]
            if key not in seen:
                seen.add(key)
                cross.append({
                    "adset_name":    row["adset_name"],
                    "home_segment":  seg,
                    "campaign_name": row["campaign_name"],
                    "spend":         row["spend"],
                    "fsd":           row["fsd"],
                })

    return cross


def find_leads_adsets(all_rows: list[dict]) -> list[dict]:
    """
    For the LEADS campaigns, return a summary of adsets with their segment attribution.
    Aggregate by adset_id across all LEADS campaigns.
    """
    leads_agg: dict[str, dict] = {}
    for row in all_rows:
        if "LEADS" not in row["campaign_name"]:
            continue
        key = row["adset_id"]
        if key not in leads_agg:
            leads_agg[key] = {
                "adset_name":  row["adset_name"],
                "campaign_name": row["campaign_name"],
                "segment":     classify_segment(row["adset_name"]) or "Unclassified",
                "spend":       0.0,
                "fsd":         0.0,
            }
        leads_agg[key]["spend"] += row["spend"]
        leads_agg[key]["fsd"]   += row["fsd"]

    return sorted(leads_agg.values(), key=lambda x: -x["spend"])


# ---------------------------------------------------------------------------
# 6. HTML helpers
# ---------------------------------------------------------------------------

def _fmt_spend(v: float) -> str:
    return f"${v:,.2f}"

def _fmt_num(v: int | float) -> str:
    return f"{int(v):,}"

def _fmt_pct(v: float) -> str:
    return f"{v:.2f}%"

def _fmt_cpa(spend: float, count: float) -> str:
    if count > 0:
        return f"${spend / count:,.2f}"
    return "—"

def _color_dot(color: str) -> str:
    return (
        f'<span style="display:inline-block;width:10px;height:10px;'
        f'border-radius:50%;background:{color};margin-right:8px;'
        f'flex-shrink:0;vertical-align:middle;"></span>'
    )

def _status_badge(rank: int, fsd: float, spend: float) -> str:
    if rank < 3:
        return '<span style="background:#422006;color:#f59e0b;border:1px solid #78350f;border-radius:20px;padding:3px 10px;font-size:.72rem;font-weight:600;white-space:nowrap;">&#127942; Top 3</span>'
    if fsd == 0 and spend < 50:
        return '<span style="background:#1a1f2e;color:#64748b;border:1px solid #2a3040;border-radius:20px;padding:3px 10px;font-size:.72rem;font-weight:600;white-space:nowrap;">&#9679; No Data</span>'
    if fsd == 0:
        return '<span style="background:#2a1a0a;color:#f59e0b;border:1px solid #78350f44;border-radius:20px;padding:3px 10px;font-size:.72rem;font-weight:600;white-space:nowrap;">&#9888; Paused</span>'
    if spend < 200:
        return '<span style="background:#0c1f2e;color:#38bdf8;border:1px solid #0369a144;border-radius:20px;padding:3px 10px;font-size:.72rem;font-weight:600;white-space:nowrap;">&#128300; Limited</span>'
    return '<span style="background:#0d1f0d;color:#34d399;border:1px solid #05603444;border-radius:20px;padding:3px 10px;font-size:.72rem;font-weight:600;white-space:nowrap;">&#10003; Active</span>'


def _td(content: str, align: str = "right", muted: bool = False) -> str:
    color = "#94a3b8" if muted else "#e4e7ef"
    return (
        f'<td style="padding:11px 14px;text-align:{align};color:{color};'
        f'border-bottom:1px solid #111827;white-space:nowrap;">{content}</td>'
    )


def _th(label: str, align: str = "right") -> str:
    return (
        f'<th style="padding:8px 14px;text-align:{align};color:#475569;'
        f'font-size:.72rem;font-weight:500;text-transform:uppercase;'
        f'letter-spacing:.07em;border-bottom:1px solid #1e2235;">{label}</th>'
    )


def _funnel_step(
    label: str,
    value: str,
    sub: str,
    color: str,
    width_pct: int = 100,
) -> str:
    return f"""
    <div style="margin-bottom:6px;">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px;">
        <span style="color:#64748b;font-size:.75rem;text-transform:uppercase;letter-spacing:.07em;">{label}</span>
        <span style="color:#64748b;font-size:.75rem;">{sub}</span>
      </div>
      <div style="background:{color}22;border:1px solid {color}55;border-radius:6px;
                  padding:8px 14px;width:{width_pct}%;">
        <span style="color:{color};font-size:1.1rem;font-weight:700;">{value}</span>
      </div>
    </div>"""


# ---------------------------------------------------------------------------
# 7. Report assembly
# ---------------------------------------------------------------------------

def build_report(
    agg: dict[str, dict],
    stripe: dict[str, int],
    cross: list[dict],
    leads_adsets: list[dict],
    all_rows: list[dict],
) -> str:

    # Sort segments by FSD descending
    sorted_segs = sorted(agg.keys(), key=lambda s: -agg[s]["total_fsd"])

    # ── Totals ────────────────────────────────────────────────────────────────
    total_spend   = sum(a["total_spend"] for a in agg.values())
    total_fsd     = sum(a["total_fsd"] for a in agg.values())
    total_paid    = sum(stripe.values())
    overall_cpac  = _fmt_cpa(total_spend, total_paid)

    # ── Summary strip ─────────────────────────────────────────────────────────
    summary_html = f"""
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:1px;
                background:#1e2235;border-radius:10px;overflow:hidden;margin-bottom:40px;">
      <div style="background:#0d1018;padding:20px 24px;text-align:center;">
        <div style="color:#475569;font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;">Total Spend</div>
        <div style="color:#f59e0b;font-size:1.8rem;font-weight:700;">{_fmt_spend(total_spend)}</div>
      </div>
      <div style="background:#0d1018;padding:20px 24px;text-align:center;">
        <div style="color:#475569;font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;">Total FSD (Form Submissions)</div>
        <div style="color:#e4e7ef;font-size:1.8rem;font-weight:700;">{_fmt_num(total_fsd)}</div>
      </div>
      <div style="background:#0d1018;padding:20px 24px;text-align:center;">
        <div style="color:#475569;font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;">Total Paid Deposits</div>
        <div style="color:#34d399;font-size:1.8rem;font-weight:700;">{total_paid}</div>
      </div>
      <div style="background:#0d1018;padding:20px 24px;text-align:center;">
        <div style="color:#475569;font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;">Overall CPaC</div>
        <div style="color:#e4e7ef;font-size:1.8rem;font-weight:700;">{overall_cpac}</div>
      </div>
    </div>"""

    # ── Segment Rankings Table ────────────────────────────────────────────────
    table_rows_html = ""
    for rank, seg in enumerate(sorted_segs):
        a     = agg[seg]
        paid  = stripe.get(seg, 0)
        color = SEGMENT_COLORS.get(seg, "#94a3b8")
        fsd   = a["total_fsd"]
        spend = a["total_spend"]

        paid_rate = (paid / fsd * 100) if fsd > 0 else 0.0
        cpac_str  = _fmt_cpa(spend, paid)
        badge     = _status_badge(rank, fsd, spend)

        table_rows_html += f"""
        <tr style="{'background:#0f1520;' if rank % 2 == 0 else ''}">
          {_td(
              f'{_color_dot(color)}'
              f'<span style="color:{color};font-weight:600;">{seg}</span>',
              align="left"
          )}
          {_td(_fmt_spend(spend))}
          {_td(_fmt_num(a["total_impr"]), muted=True)}
          {_td(_fmt_num(a["total_link_clicks"]), muted=True)}
          {_td(_fmt_pct(a["avg_ctr"]))}
          {_td(_fmt_spend(a["avg_cpc"]), muted=True)}
          {_td(_fmt_spend(a["avg_cpm"]), muted=True)}
          {_td(str(int(fsd)), align="right")}
          {_td(str(paid), align="right")}
          {_td(_fmt_pct(paid_rate), muted=(paid_rate == 0))}
          {_td(cpac_str)}
          <td style="padding:11px 14px;text-align:center;border-bottom:1px solid #111827;">{badge}</td>
        </tr>"""

    rankings_html = f"""
    <section style="margin-bottom:48px;">
      <h2 style="color:#fff;font-size:1.15rem;font-weight:700;margin:0 0 6px;">
        Segment Rankings — Sorted by FSD
      </h2>
      <p style="color:#475569;font-size:.82rem;margin:0 0 20px;">
        All ad sets classified to their home segment by name prefix. Aggregated May 11–31, 2026.
      </p>
      <div style="overflow-x:auto;">
        <table style="width:100%;border-collapse:collapse;min-width:900px;">
          <thead style="background:#080b12;">
            <tr>
              {_th("Segment", align="left")}
              {_th("Spend")}
              {_th("Impressions")}
              {_th("Link Clicks")}
              {_th("Avg CTR")}
              {_th("Avg CPC")}
              {_th("Avg CPM")}
              {_th("FSD")}
              {_th("Paid Deposits")}
              {_th("Paid Rate")}
              {_th("CPaC")}
              {_th("Status", align="center")}
            </tr>
          </thead>
          <tbody>
            {table_rows_html}
          </tbody>
        </table>
      </div>
    </section>"""

    # ── Top 3 Deep Dives ─────────────────────────────────────────────────────
    top3_segs = sorted_segs[:3]
    top3_cards_html = ""

    deep_dive_insights = {
        "Nostalgia Bridge Dad": "Strongest CTR in the cohort (11%+). Emotional nostalgia angle self-selects highly motivated fathers — lowest CPC, highest paid deposits.",
        "Sturdy Parenting":     "Evidence-based parents show the highest FSD-to-paid conversion rate. Quality over quantity — every form submission represents genuine purchase intent.",
        "Routine-Chaos":        "Consistent delivery across the full test window with no drop-off. Predictable, scalable segment — optimizing creative angle can close the gap on Nostalgia's CPaC.",
    }

    for seg in top3_segs:
        a     = agg[seg]
        paid  = stripe.get(seg, 0)
        color = SEGMENT_COLORS.get(seg, "#94a3b8")
        fsd   = a["total_fsd"]
        spend = a["total_spend"]
        impr  = a["total_impr"]
        clicks = a["total_link_clicks"]

        paid_rate = (paid / fsd * 100) if fsd > 0 else 0.0
        cpac_str  = _fmt_cpa(spend, paid)
        insight   = deep_dive_insights.get(seg, "")

        # Campaign contributions
        camp_rows = ""
        for cname, cspend in sorted(a["campaigns"].items(), key=lambda x: -x[1]):
            if cspend < 0.01:
                continue
            pct_of_seg = (cspend / spend * 100) if spend > 0 else 0
            camp_rows += f"""
            <div style="display:flex;justify-content:space-between;align-items:center;
                        padding:8px 14px;background:#080b12;border-radius:6px;margin-bottom:4px;">
              <span style="color:#94a3b8;font-size:.82rem;">{cname}</span>
              <span style="color:{color};font-size:.82rem;font-weight:600;">{_fmt_spend(cspend)}</span>
              <span style="color:#475569;font-size:.75rem;">{pct_of_seg:.1f}%</span>
            </div>"""

        # Funnel
        ctr_pct  = (clicks / impr * 100) if impr else 0
        sub_rate = (fsd / clicks * 100) if clicks else 0

        funnel_w1, funnel_w2, funnel_w3, funnel_w4 = 100, 80, 55, 35

        funnel_html = (
            _funnel_step("Impressions", _fmt_num(impr), "", color, funnel_w1)
            + f'<div style="text-align:center;color:#334155;margin:2px 0;">&#9660;</div>'
            + _funnel_step("Link Clicks", _fmt_num(clicks), f"CTR {ctr_pct:.2f}%", color, funnel_w2)
            + f'<div style="text-align:center;color:#334155;margin:2px 0;">&#9660;</div>'
            + _funnel_step("Form Submissions (FSD)", str(int(fsd)), f"{sub_rate:.1f}% of clicks", color, funnel_w3)
            + f'<div style="text-align:center;color:#334155;margin:2px 0;">&#9660;</div>'
            + _funnel_step("Paid Deposits", str(paid), f"{paid_rate:.1f}% paid rate · CPaC {cpac_str}", color, funnel_w4)
        )

        top3_cards_html += f"""
        <div style="background:#0d1018;border:1px solid {color}33;border-radius:14px;
                    margin-bottom:28px;overflow:hidden;">
          <div style="background:linear-gradient(120deg,{color}18 0%,transparent 55%);
                      padding:22px 28px;border-bottom:1px solid #1e2235;">
            <div style="color:{color};font-size:.68rem;text-transform:uppercase;
                        letter-spacing:.12em;margin-bottom:4px;">Top Segment</div>
            <h3 style="color:#fff;font-size:1.1rem;font-weight:700;margin:0;">{seg}</h3>
          </div>
          <div style="padding:24px 28px;display:grid;grid-template-columns:1fr 1fr 1fr;gap:24px;">

            <!-- Campaign contributions -->
            <div>
              <p style="color:#64748b;font-size:.72rem;text-transform:uppercase;
                        letter-spacing:.08em;margin:0 0 12px;">Campaign Contributions</p>
              {camp_rows if camp_rows else '<p style="color:#475569;font-size:.82rem;">No campaign data</p>'}
            </div>

            <!-- Funnel -->
            <div>
              <p style="color:#64748b;font-size:.72rem;text-transform:uppercase;
                        letter-spacing:.08em;margin:0 0 12px;">Conversion Funnel</p>
              {funnel_html}
            </div>

            <!-- Key insight -->
            <div>
              <p style="color:#64748b;font-size:.72rem;text-transform:uppercase;
                        letter-spacing:.08em;margin:0 0 12px;">Key Insight</p>
              <div style="background:{color}0d;border:1px solid {color}30;border-radius:8px;
                          padding:14px 16px;">
                <p style="color:#cbd5e1;font-size:.88rem;line-height:1.7;margin:0;">
                  {insight}
                </p>
              </div>
              <div style="margin-top:16px;display:grid;grid-template-columns:1fr 1fr;gap:8px;">
                <div style="background:#080b12;border:1px solid #1e2235;border-radius:6px;padding:10px 12px;text-align:center;">
                  <div style="color:#475569;font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px;">Total Spend</div>
                  <div style="color:{color};font-size:1rem;font-weight:700;">{_fmt_spend(spend)}</div>
                </div>
                <div style="background:#080b12;border:1px solid #1e2235;border-radius:6px;padding:10px 12px;text-align:center;">
                  <div style="color:#475569;font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px;">CPaC</div>
                  <div style="color:#e4e7ef;font-size:1rem;font-weight:700;">{cpac_str}</div>
                </div>
              </div>
            </div>

          </div>
        </div>"""

    deep_dive_html = f"""
    <section style="margin-bottom:48px;">
      <h2 style="color:#fff;font-size:1.15rem;font-weight:700;margin:0 0 6px;">Top 3 Segment Deep Dive</h2>
      <p style="color:#475569;font-size:.82rem;margin:0 0 24px;">
        Detailed funnel and campaign breakdown for the top performers by FSD.
      </p>
      {top3_cards_html}
    </section>"""

    # ── Cross-Campaign Ad Sets ────────────────────────────────────────────────
    cross_rows_html = ""
    if cross:
        for row in sorted(cross, key=lambda x: -x["spend"]):
            color = SEGMENT_COLORS.get(row["home_segment"], "#94a3b8")
            cross_rows_html += f"""
            <tr>
              {_td(f'<span style="color:#94a3b8;font-size:.82rem;">{row["adset_name"]}</span>', align="left")}
              {_td(
                  f'{_color_dot(color)}<span style="color:{color};font-weight:600;">{row["home_segment"]}</span>',
                  align="left"
              )}
              {_td(f'<span style="color:#64748b;">{row["campaign_name"]}</span>', align="left")}
              {_td(_fmt_spend(row["spend"]))}
              {_td(str(int(row["fsd"])))}
            </tr>"""
    else:
        cross_rows_html = '<tr><td colspan="5" style="padding:16px;color:#475569;text-align:center;">No cross-campaign ad sets detected in SALES campaigns.</td></tr>'

    # LEADS section - show adsets attributed to segments
    leads_rows_html = ""
    if leads_adsets:
        for row in leads_adsets:
            seg   = row["segment"]
            color = SEGMENT_COLORS.get(seg, "#94a3b8")
            leads_rows_html += f"""
            <tr>
              {_td(f'<span style="color:#94a3b8;font-size:.82rem;">{row["adset_name"] or row.get("adset_id","?")}</span>', align="left")}
              {_td(
                  f'{_color_dot(color)}<span style="color:{color};font-weight:600;">{seg}</span>',
                  align="left"
              )}
              {_td(f'<span style="color:#64748b;">{row["campaign_name"]}</span>', align="left")}
              {_td(_fmt_spend(row["spend"]))}
              {_td(str(int(row["fsd"])))}
            </tr>"""

    cross_campaign_html = f"""
    <section style="margin-bottom:48px;">
      <h2 style="color:#fff;font-size:1.15rem;font-weight:700;margin:0 0 6px;">
        Cross-Campaign Ad Sets
      </h2>
      <p style="color:#475569;font-size:.82rem;margin:0 0 20px;">
        Ad sets appearing in a different campaign than their home segment (SALES campaigns only),
        plus LEADS campaign ad sets attributed to their home segments.
      </p>

      <p style="color:#64748b;font-size:.8rem;font-weight:600;text-transform:uppercase;
                letter-spacing:.08em;margin:0 0 10px;">SALES — Ad Sets Outside Home Campaign</p>
      <div style="overflow-x:auto;margin-bottom:28px;">
        <table style="width:100%;border-collapse:collapse;min-width:700px;">
          <thead style="background:#080b12;">
            <tr>
              {_th("Ad Set Name", align="left")}
              {_th("Home Segment", align="left")}
              {_th("Parent Campaign", align="left")}
              {_th("Spend")}
              {_th("FSD")}
            </tr>
          </thead>
          <tbody>{cross_rows_html}</tbody>
        </table>
      </div>

      <p style="color:#64748b;font-size:.8rem;font-weight:600;text-transform:uppercase;
                letter-spacing:.08em;margin:0 0 10px;">LEADS Campaigns — Segment Attribution</p>
      <div style="overflow-x:auto;">
        <table style="width:100%;border-collapse:collapse;min-width:700px;">
          <thead style="background:#080b12;">
            <tr>
              {_th("Ad Set Name", align="left")}
              {_th("Home Segment", align="left")}
              {_th("Parent Campaign", align="left")}
              {_th("Spend")}
              {_th("FSD")}
            </tr>
          </thead>
          <tbody>{leads_rows_html if leads_rows_html else '<tr><td colspan="5" style="padding:16px;color:#475569;text-align:center;">No LEADS adset data.</td></tr>'}</tbody>
        </table>
      </div>

      <div style="background:#1a1d27;border-left:3px solid #334155;border-radius:0 8px 8px 0;
                  padding:12px 16px;margin-top:16px;">
        <p style="color:#64748b;font-size:.8rem;line-height:1.6;margin:0;">
          <strong style="color:#94a3b8;">Methodology:</strong>
          Segments are determined by ad set name prefix regardless of the campaign they live in.
          This means spend from LEADS campaigns (Top Static / Top Video / Top 3 PC) is attributed
          to the same segment buckets as the original SALES campaigns, giving a true total-spend view per audience.
        </p>
      </div>
    </section>"""

    # ── Assemble full HTML ────────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Segment Performance Report — May 11–31, 2026</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#080b12;color:#e4e7ef;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;line-height:1.5}}
    table{{border-collapse:collapse;width:100%}}
    tr:hover td{{background:rgba(255,255,255,.018)}}
  </style>
</head>
<body>
  <div style="max-width:1180px;margin:0 auto;padding:48px 24px;">

    <!-- Header -->
    <div style="margin-bottom:48px;padding-bottom:28px;border-bottom:1px solid #1e2235;">
      <div style="color:#f59e0b;font-size:.68rem;text-transform:uppercase;
                  letter-spacing:.15em;margin-bottom:10px;">Nowa — Audience Intelligence</div>
      <h1 style="font-size:2rem;font-weight:800;color:#fff;letter-spacing:-.02em;margin-bottom:8px;">
        Audience Segment Performance Report
      </h1>
      <p style="color:#64748b;font-size:.95rem;margin-bottom:16px;">
        All campaigns aggregated by audience segment &nbsp;&middot;&nbsp; May 11–31, 2026
      </p>
      <div style="background:#0d1018;border:1px solid #1e2235;border-radius:8px;
                  padding:12px 18px;display:flex;gap:10px;align-items:flex-start;">
        <span style="color:#60a5fa;flex-shrink:0;margin-top:1px;">&#9432;</span>
        <p style="color:#475569;font-size:.82rem;line-height:1.6;margin:0;">
          <strong style="color:#94a3b8;">Segment attribution methodology:</strong>
          Segments span multiple campaigns. Each ad set is attributed to its segment
          by name prefix (e.g. NOSTBRD, STURDY, ROUTINE) regardless of which campaign it belongs to.
          SALES campaigns each focus on one segment; LEADS campaigns (Top Static / Top Video / Top 3 PC)
          run ad sets from multiple segments simultaneously.
        </p>
      </div>
    </div>

    <!-- Summary -->
    {summary_html}

    <!-- Rankings -->
    {rankings_html}

    <!-- Deep Dive -->
    {deep_dive_html}

    <!-- Cross-Campaign -->
    {cross_campaign_html}

    <footer style="text-align:center;color:#1e2235;font-size:.72rem;padding:24px 0 0;
                   border-top:1px solid #111827;margin-top:16px;">
      Nowa Campaigns &nbsp;&middot;&nbsp; Segment Report &nbsp;&middot;&nbsp; May 11–31, 2026
    </footer>

  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 8. Main
# ---------------------------------------------------------------------------

def main() -> None:
    settings = load_settings()
    print(f"Date range: {SINCE} to {UNTIL}")
    print(f"Campaigns: {len(ALL_CAMPAIGNS)}")
    print()

    print("Loading Stripe paid deposits from DB ...")
    stripe = load_stripe_by_segment()
    for seg, paid in stripe.items():
        if paid > 0:
            print(f"  {seg}: {paid} paid")

    print()
    print("Fetching Meta API insights ...")
    all_rows, _ = pull_all_data(settings)
    print(f"\nTotal adset rows fetched: {len(all_rows)}")

    print()
    print("Aggregating by segment ...")
    agg = aggregate_by_segment(all_rows)

    cross       = find_cross_campaign_adsets(all_rows)
    leads_adsets = find_leads_adsets(all_rows)

    print(f"Cross-campaign adsets (SALES): {len(cross)}")
    print(f"LEADS adsets: {len(leads_adsets)}")

    print()
    print("Building HTML report ...")
    html = build_report(agg, stripe, cross, leads_adsets, all_rows)

    out = ROOT / "reports" / "segment_report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Report saved: {out}  ({len(html) // 1024} KB)")

    # Print summary table to console
    print()
    print(f"{'Segment':<28} {'Spend':>10} {'FSD':>6} {'Paid':>6} {'CPaC':>10}")
    print("-" * 65)
    sorted_segs = sorted(agg.keys(), key=lambda s: -agg[s]["total_fsd"])
    for seg in sorted_segs:
        a    = agg[seg]
        paid = stripe.get(seg, 0)
        cpac = f"${a['total_spend']/paid:.2f}" if paid else "—"
        print(
            f"{seg:<28} ${a['total_spend']:>9,.2f} {int(a['total_fsd']):>6} {paid:>6} {cpac:>10}"
        )


if __name__ == "__main__":
    main()
