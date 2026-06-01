"""Meta Ads Demographics Report Generator.

Pulls demographic breakdowns for the 3 specified campaigns from the Meta Insights API
and generates a self-contained dark-theme HTML report.

Breakdowns pulled:
  - Gender + Age (combined)
  - Country (all countries)
  - Region / US State (US traffic only)
  - Ad-set targeting specs: interests, custom audiences, age/gender targets

Income note: Meta removed advertiser-facing income-segment data from the Insights API.
It is NOT accessible programmatically; the report will note this clearly.

Run on server:
    cd /opt/campaigns-dashboard
    source .venv/bin/activate
    python scripts/demographics_report.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

# ── resolve project root so src.config is importable ───────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_settings  # noqa: E402

# ── campaign name search strings ─────────────────────────────────────────────
TARGET_CAMPAIGNS = [
    "Nostalgia Bridge",
    "Sturdy Parenting",
    "Routine Chaos",
]

# ── date range: last 30 days ─────────────────────────────────────────────────
TODAY = date.today()
SINCE = (TODAY - timedelta(days=30)).isoformat()
UNTIL = (TODAY - timedelta(days=1)).isoformat()


# ═══════════════════════════════════════════════════════════════════════════
# 1. DB freshness check
# ═══════════════════════════════════════════════════════════════════════════

def check_db_freshness(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT MAX(date) as latest_date, COUNT(*) as rows FROM ad_metrics WHERE ad_set_id=''")
    row = dict(cur.fetchone())
    cur.execute("SELECT MAX(date) as latest FROM ad_metrics WHERE ad_set_id=''")
    latest = cur.fetchone()["latest"]
    is_fresh = latest == UNTIL or latest == TODAY.isoformat()
    conn.close()
    return {"latest_date": latest, "rows": row["rows"], "is_fresh": is_fresh}


def get_campaign_ids(db_path: Path) -> dict[str, dict]:
    """Return {campaign_id: {name, total_spend_30d}} for target campaigns."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name FROM campaigns WHERE source='meta_ads'"
    )
    all_campaigns = {r["id"]: r["name"] for r in cur.fetchall()}

    # match by keyword
    matched: dict[str, dict] = {}
    for cid, cname in all_campaigns.items():
        for keyword in TARGET_CAMPAIGNS:
            if keyword.lower() in cname.lower():
                # get 30-day spend from DB
                cur.execute(
                    "SELECT SUM(spend) as s, SUM(impressions) as i, SUM(clicks) as c "
                    "FROM ad_metrics WHERE campaign_id=? AND date>=? AND date<=? AND ad_set_id=''",
                    (cid, SINCE, UNTIL),
                )
                agg = cur.fetchone()
                matched[cid] = {
                    "name": cname,
                    "keyword": keyword,
                    "spend_30d": agg["s"] or 0.0,
                    "impressions_30d": agg["i"] or 0,
                    "clicks_30d": agg["c"] or 0,
                }
                break
    conn.close()
    return matched


# ═══════════════════════════════════════════════════════════════════════════
# 2. Meta API helpers
# ═══════════════════════════════════════════════════════════════════════════

def _get_insights_with_breakdown(account, campaign_id: str, breakdowns: list[str]) -> list[dict]:
    params = {
        "level": "campaign",
        "filtering": [{"field": "campaign.id", "operator": "EQUAL", "value": campaign_id}],
        "breakdowns": breakdowns,
        "time_range": {"since": SINCE, "until": UNTIL},
        "limit": 500,
    }
    fields = ["campaign_id", "campaign_name", "spend", "impressions", "clicks", "reach"]
    cursor = account.get_insights(fields=fields, params=params)
    rows = []
    while True:
        rows.extend([dict(r) for r in cursor])
        if cursor.load_next_page() is False:
            break
    return rows


def _get_adset_targeting(account, campaign_id: str) -> list[dict]:
    """Fetch ad sets for a campaign and extract their targeting specs."""
    from facebook_business.adobjects.campaign import Campaign
    camp = Campaign(campaign_id)
    fields = [
        "id", "name", "status",
        "targeting",
        "promoted_object",
    ]
    params = {"limit": 100}
    try:
        adsets = camp.get_ad_sets(fields=fields, params=params)
        result = []
        for adset in adsets:
            d = dict(adset)
            result.append(d)
        return result
    except Exception as e:
        print(f"  [warn] ad sets fetch failed: {e}")
        return []


def pull_demographics(settings, campaign_ids: dict) -> dict:
    """Pull all demographic data from Meta API. Returns nested dict per campaign."""
    from facebook_business.api import FacebookAdsApi
    from facebook_business.adobjects.adaccount import AdAccount

    FacebookAdsApi.init(
        app_id=settings.meta_app_id,
        app_secret=settings.meta_app_secret.get_secret_value(),
        access_token=settings.meta_access_token.get_secret_value(),
        api_version="v24.0",
    )
    account = AdAccount(f"act_{settings.meta_ad_account_id.removeprefix('act_')}")

    data = {}
    for cid, meta in campaign_ids.items():
        print(f"\n→ Fetching demographics for: {meta['name']}")

        print("  • gender + age breakdown …")
        gender_age = _get_insights_with_breakdown(account, cid, ["gender", "age"])

        print("  • country breakdown …")
        country = _get_insights_with_breakdown(account, cid, ["country"])

        print("  • region breakdown (US states) …")
        region = _get_insights_with_breakdown(account, cid, ["region", "country"])

        print("  • ad set targeting specs …")
        adsets = _get_adset_targeting(account, cid)

        data[cid] = {
            **meta,
            "gender_age": gender_age,
            "country": country,
            "region": [r for r in region if r.get("country") == "US"],
            "adsets": adsets,
        }
    return data


# ═══════════════════════════════════════════════════════════════════════════
# 3. HTML generation helpers
# ═══════════════════════════════════════════════════════════════════════════

def _pct(value: float, total: float) -> str:
    if total == 0:
        return "0.0%"
    return f"{value / total * 100:.1f}%"


def _bar(pct_str: str, color: str = "#60a5fa") -> str:
    """Mini inline progress bar."""
    val = float(pct_str.replace("%", ""))
    return (
        f'<div style="display:flex;align-items:center;gap:8px;">'
        f'<div style="flex:1;height:8px;background:#1e2235;border-radius:4px;">'
        f'<div style="width:{val:.1f}%;height:100%;background:{color};border-radius:4px;"></div>'
        f'</div>'
        f'<span style="color:#ccc;font-size:.8rem;min-width:48px;">{pct_str}</span>'
        f'</div>'
    )


def _build_gender_age_section(rows: list[dict]) -> str:
    # Aggregate spend by gender
    gender_spend: dict[str, float] = {}
    gender_impr: dict[str, float] = {}
    age_spend: dict[str, float] = {}

    for r in rows:
        g = r.get("gender", "unknown").capitalize()
        a = r.get("age", "unknown")
        s = float(r.get("spend", 0) or 0)
        imp = int(r.get("impressions", 0) or 0)
        gender_spend[g] = gender_spend.get(g, 0) + s
        gender_impr[g] = gender_impr.get(g, 0) + imp
        age_spend[a] = age_spend.get(a, 0) + s

    total_spend = sum(gender_spend.values())
    total_impr = sum(gender_impr.values())

    # Gender card
    gender_rows_html = ""
    gender_colors = {"Male": "#60a5fa", "Female": "#f472b6", "Unknown": "#94a3b8"}
    for g, s in sorted(gender_spend.items(), key=lambda x: -x[1]):
        color = gender_colors.get(g, "#94a3b8")
        sp_pct = _pct(s, total_spend)
        imp_pct = _pct(gender_impr.get(g, 0), total_impr)
        gender_rows_html += f"""
        <tr>
          <td style="padding:8px 12px;color:{color};font-weight:600;">{g}</td>
          <td style="padding:8px 12px;">${s:,.2f}</td>
          <td style="padding:8px 12px;">{_bar(sp_pct, color)}</td>
          <td style="padding:8px 12px;color:#94a3b8;">{int(gender_impr.get(g,0)):,} ({imp_pct})</td>
        </tr>"""

    # Age chart
    age_html = ""
    age_colors = ["#60a5fa","#818cf8","#a78bfa","#c084fc","#e879f9","#f472b6","#fb7185","#fb923c"]
    top_ages = sorted(age_spend.items(), key=lambda x: -x[1])
    total_age = sum(age_spend.values())
    for i, (a, s) in enumerate(top_ages):
        color = age_colors[i % len(age_colors)]
        age_html += f"""
        <div style="margin-bottom:10px;">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
            <span style="color:#e4e7ef;">{a}</span>
            <span style="color:#94a3b8;">${s:,.2f}</span>
          </div>
          {_bar(_pct(s, total_age), color)}
        </div>"""

    if not gender_rows_html:
        gender_rows_html = '<tr><td colspan="4" style="padding:16px;color:#666;text-align:center;">No data</td></tr>'
    if not age_html:
        age_html = '<p style="color:#666;">No data</p>'

    return f"""
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:16px;">
      <div>
        <h4 style="color:#94a3b8;font-size:.75rem;text-transform:uppercase;letter-spacing:.08em;margin:0 0 12px;">Gender Split (by spend)</h4>
        <table style="width:100%;border-collapse:collapse;">
          <thead>
            <tr style="border-bottom:1px solid #2a2e3a;">
              <th style="padding:6px 12px;text-align:left;color:#64748b;font-size:.75rem;">Gender</th>
              <th style="padding:6px 12px;text-align:left;color:#64748b;font-size:.75rem;">Spend</th>
              <th style="padding:6px 12px;text-align:left;color:#64748b;font-size:.75rem;">% of Spend</th>
              <th style="padding:6px 12px;text-align:left;color:#64748b;font-size:.75rem;">Impressions</th>
            </tr>
          </thead>
          <tbody>{gender_rows_html}</tbody>
        </table>
      </div>
      <div>
        <h4 style="color:#94a3b8;font-size:.75rem;text-transform:uppercase;letter-spacing:.08em;margin:0 0 12px;">Age Breakdown (by spend)</h4>
        {age_html}
      </div>
    </div>"""


def _build_country_section(rows: list[dict]) -> str:
    country_spend: dict[str, float] = {}
    country_impr: dict[str, int] = {}
    for r in rows:
        c = r.get("country", "??")
        s = float(r.get("spend", 0) or 0)
        imp = int(r.get("impressions", 0) or 0)
        country_spend[c] = country_spend.get(c, 0) + s
        country_impr[c] = country_impr.get(c, 0) + imp

    total = sum(country_spend.values())
    top = sorted(country_spend.items(), key=lambda x: -x[1])[:15]

    rows_html = ""
    bar_colors = ["#34d399","#60a5fa","#f59e0b","#f472b6","#818cf8","#fb923c"]
    for i, (c, s) in enumerate(top):
        color = bar_colors[i % len(bar_colors)]
        rows_html += f"""
        <tr style="border-bottom:1px solid #1e2235;">
          <td style="padding:8px 12px;color:#e4e7ef;font-weight:500;">{c}</td>
          <td style="padding:8px 12px;">${s:,.2f}</td>
          <td style="padding:8px 12px;min-width:180px;">{_bar(_pct(s, total), color)}</td>
          <td style="padding:8px 12px;color:#94a3b8;">{country_impr.get(c,0):,}</td>
        </tr>"""

    if not rows_html:
        rows_html = '<tr><td colspan="4" style="padding:16px;color:#666;text-align:center;">No data</td></tr>'

    return f"""
    <h4 style="color:#94a3b8;font-size:.75rem;text-transform:uppercase;letter-spacing:.08em;margin:16px 0 12px;">Country Distribution (Top 15 by Spend)</h4>
    <table style="width:100%;border-collapse:collapse;">
      <thead>
        <tr style="border-bottom:1px solid #2a2e3a;">
          <th style="padding:6px 12px;text-align:left;color:#64748b;font-size:.75rem;">Country</th>
          <th style="padding:6px 12px;text-align:left;color:#64748b;font-size:.75rem;">Spend</th>
          <th style="padding:6px 12px;text-align:left;color:#64748b;font-size:.75rem;">% of Spend</th>
          <th style="padding:6px 12px;text-align:left;color:#64748b;font-size:.75rem;">Impressions</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>"""


def _build_region_section(rows: list[dict]) -> str:
    region_spend: dict[str, float] = {}
    for r in rows:
        reg = r.get("region", "Unknown")
        s = float(r.get("spend", 0) or 0)
        region_spend[reg] = region_spend.get(reg, 0) + s

    total = sum(region_spend.values())
    top = sorted(region_spend.items(), key=lambda x: -x[1])[:20]

    if not top:
        return '<p style="color:#666;font-style:italic;margin-top:12px;">No US state data available for this campaign.</p>'

    html = '<h4 style="color:#94a3b8;font-size:.75rem;text-transform:uppercase;letter-spacing:.08em;margin:20px 0 12px;">US State Breakdown (Top 20 by Spend)</h4>'
    html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">'
    for i, (reg, s) in enumerate(top):
        color = "#60a5fa" if i < 5 else "#818cf8" if i < 10 else "#94a3b8"
        html += f"""
        <div style="background:#1a1d27;border-radius:6px;padding:10px 14px;">
          <div style="display:flex;justify-content:space-between;margin-bottom:6px;">
            <span style="color:#e4e7ef;font-size:.85rem;">{reg}</span>
            <span style="color:{color};font-weight:600;font-size:.85rem;">{_pct(s, total)}</span>
          </div>
          {_bar(_pct(s, total), color)}
          <div style="color:#64748b;font-size:.75rem;margin-top:4px;">${s:,.2f}</div>
        </div>"""
    html += '</div>'
    return html


def _format_targeting_value(v) -> str:
    if isinstance(v, dict):
        return v.get("name") or v.get("id") or str(v)
    if isinstance(v, list):
        return ", ".join(_format_targeting_value(i) for i in v[:5]) + ("…" if len(v) > 5 else "")
    return str(v)


def _build_targeting_section(adsets: list[dict]) -> str:
    if not adsets:
        return '<p style="color:#666;font-style:italic;">No ad set targeting data available.</p>'

    html = ""
    for adset in adsets:
        name = adset.get("name", "Unnamed Ad Set")
        status = adset.get("status", "")
        targeting = adset.get("targeting") or {}
        if isinstance(targeting, str):
            try:
                targeting = json.loads(targeting)
            except Exception:
                targeting = {}

        status_color = "#34d399" if status == "ACTIVE" else "#f59e0b" if status == "PAUSED" else "#94a3b8"
        html += f"""
        <div style="background:#1a1d27;border:1px solid #2a2e3a;border-radius:8px;padding:16px;margin-bottom:12px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <span style="color:#e4e7ef;font-weight:600;">{name}</span>
            <span style="background:{status_color}22;color:{status_color};border:1px solid {status_color}55;
                         border-radius:4px;padding:2px 8px;font-size:.75rem;">{status}</span>
          </div>"""

        # Age + Gender targets
        age_min = targeting.get("age_min")
        age_max = targeting.get("age_max")
        genders = targeting.get("genders")
        if age_min or age_max or genders:
            age_str = f"{age_min or '?'}–{age_max or '65+'}"
            gender_map = {1: "Male", 2: "Female"}
            gender_str = ", ".join(gender_map.get(g, str(g)) for g in (genders or [])) or "All"
            html += f"""
          <div style="margin-bottom:8px;">
            <span style="color:#64748b;font-size:.75rem;text-transform:uppercase;">Target Age:</span>
            <span style="color:#60a5fa;margin-left:8px;">{age_str}</span>
            <span style="color:#64748b;font-size:.75rem;text-transform:uppercase;margin-left:16px;">Gender:</span>
            <span style="color:#f472b6;margin-left:8px;">{gender_str}</span>
          </div>"""

        # Locations
        geo = targeting.get("geo_locations") or {}
        if isinstance(geo, str):
            try:
                geo = json.loads(geo)
            except Exception:
                geo = {}
        countries = geo.get("countries") or []
        regions = geo.get("regions") or []
        if countries or regions:
            loc_str = ", ".join(countries)
            if regions:
                loc_str += " + " + ", ".join(r.get("name", str(r)) for r in regions[:5])
            html += f"""
          <div style="margin-bottom:8px;">
            <span style="color:#64748b;font-size:.75rem;text-transform:uppercase;">Locations:</span>
            <span style="color:#34d399;margin-left:8px;">{loc_str}</span>
          </div>"""

        # Interests
        interests = targeting.get("interests") or []
        if interests:
            interest_names = [i.get("name", str(i)) for i in interests[:10]]
            html += f"""
          <div style="margin-bottom:8px;">
            <span style="color:#64748b;font-size:.75rem;text-transform:uppercase;">Interests:</span>
            <div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px;">"""
            for iname in interest_names:
                html += f'<span style="background:#60a5fa22;color:#60a5fa;border:1px solid #60a5fa44;border-radius:12px;padding:2px 10px;font-size:.78rem;">{iname}</span>'
            html += "</div></div>"

        # Custom Audiences
        custom = targeting.get("custom_audiences") or []
        lookalike = targeting.get("lookalike_audience") or []
        if custom or lookalike:
            aud_items = custom + lookalike
            html += f"""
          <div style="margin-bottom:8px;">
            <span style="color:#64748b;font-size:.75rem;text-transform:uppercase;">Custom / Lookalike Audiences:</span>
            <div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px;">"""
            for aud in aud_items[:8]:
                aname = aud.get("name", aud.get("id", str(aud)))
                html += f'<span style="background:#a78bfa22;color:#a78bfa;border:1px solid #a78bfa44;border-radius:12px;padding:2px 10px;font-size:.78rem;">{aname}</span>'
            html += "</div></div>"

        # Behaviors
        behaviors = targeting.get("behaviors") or []
        if behaviors:
            html += f"""
          <div style="margin-bottom:8px;">
            <span style="color:#64748b;font-size:.75rem;text-transform:uppercase;">Behaviors:</span>
            <div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px;">"""
            for b in behaviors[:8]:
                bname = b.get("name", str(b))
                html += f'<span style="background:#f59e0b22;color:#f59e0b;border:1px solid #f59e0b44;border-radius:12px;padding:2px 10px;font-size:.78rem;">{bname}</span>'
            html += "</div></div>"

        # Advantage+ note
        if not interests and not custom and not behaviors and not age_min:
            html += '<p style="color:#64748b;font-size:.82rem;font-style:italic;">Using Advantage+ Audience (Meta auto-optimized — no explicit targeting restrictions set).</p>'

        html += "</div>"
    return html


# ═══════════════════════════════════════════════════════════════════════════
# 4. HTML report
# ═══════════════════════════════════════════════════════════════════════════

CAMPAIGN_COLORS = {
    "Nostalgia Bridge": "#f59e0b",
    "Sturdy Parenting": "#34d399",
    "Routine Chaos":    "#f472b6",
}


def _color_for(keyword: str) -> str:
    for k, c in CAMPAIGN_COLORS.items():
        if k.lower() in keyword.lower():
            return c
    return "#60a5fa"


def build_html(freshness: dict, demo_data: dict) -> str:
    fresh_badge = (
        '<span style="background:#34d39922;color:#34d399;border:1px solid #34d39944;'
        'border-radius:4px;padding:2px 8px;font-size:.8rem;margin-left:12px;">✓ Up to date</span>'
        if freshness["is_fresh"] else
        f'<span style="background:#f59e0b22;color:#f59e0b;border:1px solid #f59e0b44;'
        f'border-radius:4px;padding:2px 8px;font-size:.8rem;margin-left:12px;">'
        f'⚠ Last data: {freshness["latest_date"]}</span>'
    )

    campaign_sections = ""
    for cid, d in demo_data.items():
        color = _color_for(d["keyword"])
        ga_section = _build_gender_age_section(d["gender_age"])
        country_section = _build_country_section(d["country"])
        region_section = _build_region_section(d["region"])
        targeting_section = _build_targeting_section(d["adsets"])

        # Summary KPIs
        ctr = (d["clicks_30d"] / d["impressions_30d"] * 100) if d["impressions_30d"] else 0

        campaign_sections += f"""
    <div style="background:#12151f;border:1px solid {color}44;border-radius:12px;
                margin-bottom:32px;overflow:hidden;">

      <!-- Campaign header -->
      <div style="background:linear-gradient(135deg,{color}22 0%,transparent 60%);
                  padding:24px 28px;border-bottom:1px solid #2a2e3a;">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;">
          <div>
            <div style="color:{color};font-size:.7rem;text-transform:uppercase;
                        letter-spacing:.1em;margin-bottom:6px;">{d['keyword']}</div>
            <h2 style="margin:0;color:#fff;font-size:1.3rem;">{d['name']}</h2>
            <div style="color:#64748b;font-size:.8rem;margin-top:4px;">ID: {cid}</div>
          </div>
          <div style="display:flex;gap:16px;">
            <div style="text-align:right;">
              <div style="color:#64748b;font-size:.7rem;text-transform:uppercase;">30-day Spend</div>
              <div style="color:{color};font-size:1.4rem;font-weight:700;">${d['spend_30d']:,.2f}</div>
            </div>
            <div style="text-align:right;">
              <div style="color:#64748b;font-size:.7rem;text-transform:uppercase;">Impressions</div>
              <div style="color:#e4e7ef;font-size:1.4rem;font-weight:700;">{d['impressions_30d']:,}</div>
            </div>
            <div style="text-align:right;">
              <div style="color:#64748b;font-size:.7rem;text-transform:uppercase;">CTR</div>
              <div style="color:#e4e7ef;font-size:1.4rem;font-weight:700;">{ctr:.2f}%</div>
            </div>
          </div>
        </div>
      </div>

      <div style="padding:24px 28px;">

        <!-- Gender + Age -->
        <div style="margin-bottom:32px;">
          <h3 style="color:#e4e7ef;margin:0 0 4px;font-size:1rem;">
            <span style="color:{color};">●</span> Gender &amp; Age Demographics
          </h3>
          <p style="color:#64748b;font-size:.8rem;margin:0 0 4px;">
            Based on ad delivery (spend-weighted) over the last 30 days.
          </p>
          {ga_section}
        </div>

        <!-- Location -->
        <div style="margin-bottom:32px;">
          <h3 style="color:#e4e7ef;margin:0 0 4px;font-size:1rem;">
            <span style="color:{color};">●</span> Location Breakdown
          </h3>
          <p style="color:#64748b;font-size:.8rem;margin:0;">
            Country-level spend distribution. US traffic is broken down to state level below.
          </p>
          {country_section}
          {region_section}
        </div>

        <!-- Income note -->
        <div style="margin-bottom:32px;background:#1a1d27;border-left:3px solid #f59e0b;
                    border-radius:0 8px 8px 0;padding:14px 18px;">
          <h3 style="color:#f59e0b;margin:0 0 6px;font-size:.9rem;">⚠ Income Data</h3>
          <p style="color:#94a3b8;font-size:.83rem;margin:0;line-height:1.6;">
            Meta removed income-segment targeting data from the Insights API (deprecated 2022).
            Income brackets are no longer accessible programmatically. To view income demographics,
            use <strong style="color:#e4e7ef;">Ads Manager → Campaigns → Audience → Household Income</strong>
            in the browser — it is shown as a visual chart only, not via API.
          </p>
        </div>

        <!-- Interests + Audience Segments -->
        <div>
          <h3 style="color:#e4e7ef;margin:0 0 4px;font-size:1rem;">
            <span style="color:{color};">●</span> Audience Targeting &amp; Segments
          </h3>
          <p style="color:#64748b;font-size:.8rem;margin:0 0 12px;">
            Targeting configuration per ad set (interests, custom audiences, behaviors, geographic and demographic restrictions).
          </p>
          {targeting_section}
        </div>

      </div>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Audience Demographics Report — Nowa</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #0f1117; color: #e4e7ef; font-family: -apple-system, BlinkMacSystemFont,
            'Segoe UI', Roboto, sans-serif; line-height: 1.5; }}
    table {{ border-collapse: collapse; }}
    th {{ font-weight: 600; }}
    @media print {{
      body {{ background: #fff; color: #111; }}
    }}
  </style>
</head>
<body>
  <div style="max-width:1100px;margin:0 auto;padding:40px 24px;">

    <!-- Header -->
    <div style="margin-bottom:40px;">
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">
        <div>
          <h1 style="font-size:1.8rem;font-weight:700;color:#fff;">
            Audience Demographics Report
          </h1>
          <p style="color:#64748b;margin-top:4px;">
            Meta Ads · Last 30 days ({SINCE} → {UNTIL}) · Generated {TODAY.isoformat()}
          </p>
        </div>
        <div>
          <span style="color:#64748b;font-size:.85rem;">DB latest data: <strong style="color:#e4e7ef;">{freshness['latest_date']}</strong></span>
          {fresh_badge}
        </div>
      </div>

      <!-- Income disclaimer banner -->
      <div style="margin-top:20px;background:#1a1d27;border:1px solid #2a2e3a;border-radius:8px;
                  padding:14px 18px;display:flex;gap:12px;align-items:flex-start;">
        <span style="font-size:1.1rem;">ℹ️</span>
        <div>
          <strong style="color:#e4e7ef;">Note on Income Data:</strong>
          <span style="color:#94a3b8;font-size:.85rem;margin-left:6px;">
            Meta's Insights API does not expose household income segments.
            This report covers gender, age, country, US state breakdowns, and ad-set targeting specs.
            Income is only visible as a chart in the Meta Ads Manager UI.
          </span>
        </div>
      </div>
    </div>

    <!-- Campaigns -->
    {campaign_sections}

    <footer style="text-align:center;color:#2a2e3a;font-size:.75rem;padding:24px 0;">
      Generated by Nowa Campaigns Dashboard · {TODAY.isoformat()}
    </footer>
  </div>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════
# 5. Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    settings = load_settings()
    db_path = settings.db_path

    print(f"── DB path: {db_path}")
    freshness = check_db_freshness(db_path)
    print(f"── DB latest date: {freshness['latest_date']}  (today={TODAY.isoformat()})")
    print(f"── DB fresh: {freshness['is_fresh']}")

    if not freshness["is_fresh"]:
        print(f"⚠  DB is behind — latest is {freshness['latest_date']}, not {UNTIL}.")
        print("   Consider running the daily backfill first.")
        print("   Continuing with Meta API live pull regardless…\n")

    print(f"\n── Matching campaigns in DB for: {TARGET_CAMPAIGNS}")
    campaign_ids = get_campaign_ids(db_path)
    if not campaign_ids:
        print("✗ No matching campaigns found in the database. Aborting.")
        sys.exit(1)

    for cid, meta in campaign_ids.items():
        print(f"   ✓ {meta['name']} ({cid})")

    print("\n── Pulling demographics from Meta API …")
    demo_data = pull_demographics(settings, campaign_ids)

    print("\n── Generating HTML report …")
    html_content = build_html(freshness, demo_data)

    out_path = ROOT / "reports" / f"demographics_{TODAY.isoformat()}.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_content, encoding="utf-8")
    print(f"\n✓ Report saved to: {out_path}")
    print(f"  Size: {len(html_content) // 1024} KB")


if __name__ == "__main__":
    main()
