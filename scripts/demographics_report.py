"""Meta Ads Demographics Report Generator.

Pulls demographic breakdowns for the 3 specified campaigns directly from the
Meta Insights API (no local DB dependency) and generates a dark-theme HTML report.

Breakdowns pulled:
  - Gender + Age (spend-weighted)
  - Country (all countries, top 15)
  - Region / US State (US traffic, top 20)

Income note: Meta removed income-segment data from the Insights API (deprecated 2022).
It is NOT accessible programmatically.

Run:
    cd /path/to/ads-reporting
    python -X utf8 scripts/demographics_report.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_settings  # noqa: E402

# Campaign name keywords (case-insensitive substring match)
TARGET_CAMPAIGNS = [
    "Nostalgia Bridge",
    "Sturdy Parenting",
    "Routine",
]

TODAY = date.today()
SINCE = (TODAY - timedelta(days=30)).isoformat()
UNTIL = (TODAY - timedelta(days=1)).isoformat()

CAMPAIGN_COLORS = {
    "Nostalgia Bridge": "#f59e0b",
    "Sturdy Parenting": "#34d399",
    "Routine":          "#f472b6",
}


# ═══════════════════════════════════════════════════════════════════════════
# 1. Meta API helpers
# ═══════════════════════════════════════════════════════════════════════════

def _init_api(settings):
    from facebook_business.api import FacebookAdsApi
    FacebookAdsApi.init(
        app_id=settings.meta_app_id,
        app_secret=settings.meta_app_secret.get_secret_value(),
        access_token=settings.meta_access_token.get_secret_value(),
        api_version="v24.0",
    )


def _get_campaigns(settings) -> dict[str, dict]:
    """Search all campaigns in the ad account and return those matching TARGET_CAMPAIGNS."""
    from facebook_business.adobjects.adaccount import AdAccount
    account = AdAccount(f"act_{settings.meta_ad_account_id.removeprefix('act_')}")
    fields = ["id", "name", "status"]
    params = {"limit": 500}
    cursor = account.get_campaigns(fields=fields, params=params)
    all_camps = []
    while True:
        all_camps.extend([dict(c) for c in cursor])
        if cursor.load_next_page() is False:
            break

    matched: dict[str, dict] = {}
    for c in all_camps:
        cname = c.get("name", "")
        for keyword in TARGET_CAMPAIGNS:
            if keyword.lower() in cname.lower():
                matched[c["id"]] = {
                    "name": cname,
                    "keyword": keyword,
                    "status": c.get("status", ""),
                }
                break
    return matched


def _get_campaign_targeting(campaign_id: str) -> dict:
    """Pull all ad sets for a campaign and merge targeting into a single deduplicated dict."""
    import json as _json
    from facebook_business.adobjects.campaign import Campaign

    camp = Campaign(campaign_id)
    fields = ["id", "name", "targeting"]
    try:
        cursor = camp.get_ad_sets(fields=fields, params={"limit": 100})
        adsets = []
        while True:
            adsets.extend([dict(a) for a in cursor])
            if cursor.load_next_page() is False:
                break
    except Exception as e:
        print(f"  [warn] targeting fetch failed: {e}")
        return {}

    # Aggregate across all ad sets — deduplicate by name/id
    interests:    dict[str, str] = {}   # id -> name
    behaviors:    dict[str, str] = {}
    custom_auds:  dict[str, str] = {}
    exclusions_i: dict[str, str] = {}
    exclusions_b: dict[str, str] = {}
    exclusions_a: dict[str, str] = {}
    flex_specs:   list[list[str]] = []   # each entry = one AND-group of OR-interests
    locations:    set[str] = set()
    age_mins:     set[int] = set()
    age_maxs:     set[int] = set()
    genders:      set[str] = set()
    adv_plus_count = 0

    GENDER_MAP = {1: "Male", 2: "Female"}

    for adset in adsets:
        t = adset.get("targeting") or {}
        if isinstance(t, str):
            try:
                t = _json.loads(t)
            except Exception:
                t = {}

        # Age + gender
        age_min = t.get("age_min")
        age_max = t.get("age_max")
        if age_min: age_mins.add(int(age_min))
        if age_max: age_maxs.add(int(age_max))
        for g in (t.get("genders") or []):
            genders.add(GENDER_MAP.get(g, str(g)))

        # Geo
        geo = t.get("geo_locations") or {}
        if isinstance(geo, str):
            try: geo = _json.loads(geo)
            except Exception: geo = {}
        for c in (geo.get("countries") or []):
            locations.add(c)
        for r in (geo.get("regions") or []):
            locations.add(r.get("name", str(r)))

        # Interests (standard detailed targeting)
        for item in (t.get("interests") or []):
            iid = str(item.get("id", ""))
            if iid: interests[iid] = item.get("name", iid)

        # Flexible spec (AND/OR interest groups)
        for grp in (t.get("flexible_spec") or []):
            grp_interests = [i.get("name", "") for i in (grp.get("interests") or []) if i.get("name")]
            grp_behaviors = [b.get("name", "") for b in (grp.get("behaviors") or []) if b.get("name")]
            combined = grp_interests + grp_behaviors
            if combined:
                flex_specs.append(combined)
                for i in (grp.get("interests") or []):
                    iid = str(i.get("id", ""))
                    if iid: interests[iid] = i.get("name", iid)
                for b in (grp.get("behaviors") or []):
                    bid = str(b.get("id", ""))
                    if bid: behaviors[bid] = b.get("name", bid)

        # Behaviors
        for item in (t.get("behaviors") or []):
            bid = str(item.get("id", ""))
            if bid: behaviors[bid] = item.get("name", bid)

        # Custom / lookalike audiences
        for item in (t.get("custom_audiences") or []):
            aid = str(item.get("id", ""))
            if aid: custom_auds[aid] = item.get("name", aid)

        # Exclusions
        excl = t.get("exclusions") or {}
        if isinstance(excl, str):
            try: excl = _json.loads(excl)
            except Exception: excl = {}
        for item in (excl.get("interests") or []):
            eid = str(item.get("id", ""))
            if eid: exclusions_i[eid] = item.get("name", eid)
        for item in (excl.get("behaviors") or []):
            eid = str(item.get("id", ""))
            if eid: exclusions_b[eid] = item.get("name", eid)
        for item in (excl.get("custom_audiences") or []):
            eid = str(item.get("id", ""))
            if eid: exclusions_a[eid] = item.get("name", eid)

        # Advantage+ signal
        if not t.get("interests") and not t.get("behaviors") and not t.get("custom_audiences") \
                and not t.get("flexible_spec"):
            adv_plus_count += 1

    exclusions = list(exclusions_i.values()) + list(exclusions_b.values()) + list(exclusions_a.values())

    # Collapse age ranges to a single "min–max" span
    age_range_str = ""
    if age_mins or age_maxs:
        lo = min(age_mins) if age_mins else 18
        hi = max(age_maxs) if age_maxs else 65
        age_range_str = f"{lo}–{hi}+"

    return {
        "interests":      list(interests.values()),
        "behaviors":      list(behaviors.values()),
        "custom_auds":    list(custom_auds.values()),
        "exclusions":     exclusions,
        "flex_specs":     flex_specs,
        "locations":      sorted(locations),
        "age_range":      age_range_str,
        "genders":        sorted(genders),
        "adset_count":    len(adsets),
        "advantage_plus": adv_plus_count == len(adsets) and len(adsets) > 0,
    }


def _insights_with_breakdown(account, campaign_id: str, breakdowns: list[str]) -> list[dict]:
    params = {
        "level": "campaign",
        "filtering": [{"field": "campaign.id", "operator": "EQUAL", "value": campaign_id}],
        "breakdowns": breakdowns,
        "time_range": {"since": SINCE, "until": UNTIL},
        "limit": 500,
    }
    fields = ["campaign_id", "spend", "impressions", "clicks", "reach"]
    cursor = account.get_insights(fields=fields, params=params)
    rows = []
    while True:
        rows.extend([dict(r) for r in cursor])
        if cursor.load_next_page() is False:
            break
    return rows


def pull_all(settings) -> dict:
    from facebook_business.adobjects.adaccount import AdAccount
    _init_api(settings)
    account = AdAccount(f"act_{settings.meta_ad_account_id.removeprefix('act_')}")

    print("Finding campaigns from Meta API ...")
    campaigns = _get_campaigns(settings)
    if not campaigns:
        print("No matching campaigns found.")
        sys.exit(1)
    for cid, m in campaigns.items():
        print(f"  OK  {m['name']}  ({cid})")

    data = {}
    for cid, meta in campaigns.items():
        print(f"\nFetching: {meta['name']}")

        # Overall totals (no breakdown) for KPI strip
        print("  totals ...")
        totals_rows = _insights_with_breakdown(account, cid, [])
        spend_total = sum(float(r.get("spend", 0) or 0) for r in totals_rows)
        impr_total  = sum(int(r.get("impressions", 0) or 0) for r in totals_rows)
        clicks_total= sum(int(r.get("clicks", 0) or 0) for r in totals_rows)

        print("  gender + age ...")
        gender_age = _insights_with_breakdown(account, cid, ["gender", "age"])

        print("  country ...")
        country = _insights_with_breakdown(account, cid, ["country"])

        print("  US states ...")
        region_all = _insights_with_breakdown(account, cid, ["region", "country"])
        region_us = [r for r in region_all if r.get("country") == "US"]

        print("  targeting / audience segments ...")
        targeting = _get_campaign_targeting(cid)

        data[cid] = {
            **meta,
            "spend_30d":   spend_total,
            "impr_30d":    impr_total,
            "clicks_30d":  clicks_total,
            "gender_age":  gender_age,
            "country":     country,
            "region_us":   region_us,
            "targeting":   targeting,
        }
    return data


# ═══════════════════════════════════════════════════════════════════════════
# 2. Chart / table helpers
# ═══════════════════════════════════════════════════════════════════════════

def _pct(value: float, total: float) -> str:
    if total == 0:
        return "0.0%"
    return f"{value / total * 100:.1f}%"


def _bar(pct_str: str, color: str = "#60a5fa") -> str:
    val = float(pct_str.replace("%", ""))
    return (
        f'<div style="display:flex;align-items:center;gap:8px;">'
        f'<div style="flex:1;height:8px;background:#1e2235;border-radius:4px;">'
        f'<div style="width:{val:.1f}%;height:100%;background:{color};border-radius:4px;"></div>'
        f'</div>'
        f'<span style="color:#94a3b8;font-size:.78rem;min-width:44px;">{pct_str}</span>'
        f'</div>'
    )


def _gender_age_html(rows: list[dict]) -> str:
    gender_spend: dict[str, float] = {}
    gender_impr:  dict[str, float] = {}
    age_spend:    dict[str, float] = {}

    for r in rows:
        g = r.get("gender", "unknown").capitalize()
        a = r.get("age", "unknown")
        s = float(r.get("spend", 0) or 0)
        i = int(r.get("impressions", 0) or 0)
        gender_spend[g] = gender_spend.get(g, 0) + s
        gender_impr[g]  = gender_impr.get(g, 0) + i
        age_spend[a]    = age_spend.get(a, 0) + s

    total_spend = sum(gender_spend.values()) or 1
    total_impr  = sum(gender_impr.values()) or 1
    total_age   = sum(age_spend.values()) or 1

    GCOL = {"Male": "#60a5fa", "Female": "#f472b6", "Unknown": "#94a3b8"}
    ACOL = ["#60a5fa","#818cf8","#a78bfa","#c084fc","#e879f9","#f472b6","#fb7185","#fb923c"]

    # Gender rows
    g_rows = ""
    for g, s in sorted(gender_spend.items(), key=lambda x: -x[1]):
        col = GCOL.get(g, "#94a3b8")
        g_rows += f"""
        <tr style="border-bottom:1px solid #1e2235;">
          <td style="padding:9px 14px;color:{col};font-weight:600;">{g}</td>
          <td style="padding:9px 14px;color:#e4e7ef;">${s:,.2f}</td>
          <td style="padding:9px 14px;min-width:160px;">{_bar(_pct(s, total_spend), col)}</td>
          <td style="padding:9px 14px;color:#94a3b8;">{int(gender_impr.get(g,0)):,} &nbsp;<span style="color:#475569;font-size:.75rem;">({_pct(gender_impr.get(g,0), total_impr)})</span></td>
        </tr>"""
    if not g_rows:
        g_rows = '<tr><td colspan="4" style="padding:16px;color:#475569;text-align:center;">No data</td></tr>'

    # Age bars
    a_bars = ""
    for i, (a, s) in enumerate(sorted(age_spend.items(), key=lambda x: -x[1])):
        col = ACOL[i % len(ACOL)]
        a_bars += f"""
        <div style="margin-bottom:12px;">
          <div style="display:flex;justify-content:space-between;margin-bottom:5px;">
            <span style="color:#e4e7ef;font-size:.88rem;">{a}</span>
            <span style="color:#64748b;font-size:.82rem;">${s:,.2f} &nbsp;·&nbsp; {_pct(s, total_age)}</span>
          </div>
          {_bar(_pct(s, total_age), col)}
        </div>"""
    if not a_bars:
        a_bars = '<p style="color:#475569;">No data</p>'

    return f"""
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:20px;">
      <div>
        <p style="color:#64748b;font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;margin:0 0 10px;">Gender Split</p>
        <table style="width:100%;border-collapse:collapse;">
          <thead>
            <tr style="border-bottom:1px solid #2a2e3a;">
              <th style="padding:6px 14px;text-align:left;color:#475569;font-size:.72rem;font-weight:500;">Gender</th>
              <th style="padding:6px 14px;text-align:left;color:#475569;font-size:.72rem;font-weight:500;">Spend</th>
              <th style="padding:6px 14px;text-align:left;color:#475569;font-size:.72rem;font-weight:500;">% Spend</th>
              <th style="padding:6px 14px;text-align:left;color:#475569;font-size:.72rem;font-weight:500;">Impressions</th>
            </tr>
          </thead>
          <tbody>{g_rows}</tbody>
        </table>
      </div>
      <div>
        <p style="color:#64748b;font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;margin:0 0 10px;">Age Brackets (by spend)</p>
        {a_bars}
      </div>
    </div>"""


def _country_html(rows: list[dict]) -> str:
    spend: dict[str, float] = {}
    impr:  dict[str, int]   = {}
    for r in rows:
        c = r.get("country", "??")
        spend[c] = spend.get(c, 0) + float(r.get("spend", 0) or 0)
        impr[c]  = impr.get(c, 0)  + int(r.get("impressions", 0) or 0)

    total = sum(spend.values()) or 1
    top   = sorted(spend.items(), key=lambda x: -x[1])[:15]
    COLS  = ["#34d399","#60a5fa","#f59e0b","#f472b6","#818cf8","#fb923c","#38bdf8","#a3e635"]

    rows_html = ""
    for i, (c, s) in enumerate(top):
        col = COLS[i % len(COLS)]
        rows_html += f"""
        <tr style="border-bottom:1px solid #1a1d27;">
          <td style="padding:9px 14px;">
            <span style="color:#e4e7ef;font-weight:500;">{c}</span>
          </td>
          <td style="padding:9px 14px;color:#e4e7ef;">${s:,.2f}</td>
          <td style="padding:9px 14px;min-width:200px;">{_bar(_pct(s, total), col)}</td>
          <td style="padding:9px 14px;color:#64748b;">{impr.get(c,0):,}</td>
        </tr>"""
    if not rows_html:
        rows_html = '<tr><td colspan="4" style="padding:16px;color:#475569;text-align:center;">No country data</td></tr>'

    return f"""
    <p style="color:#64748b;font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;margin:0 0 10px;">Country Distribution (Top 15 by Spend)</p>
    <table style="width:100%;border-collapse:collapse;">
      <thead>
        <tr style="border-bottom:1px solid #2a2e3a;background:#12151f;">
          <th style="padding:7px 14px;text-align:left;color:#475569;font-size:.72rem;font-weight:500;">Country</th>
          <th style="padding:7px 14px;text-align:left;color:#475569;font-size:.72rem;font-weight:500;">Spend</th>
          <th style="padding:7px 14px;text-align:left;color:#475569;font-size:.72rem;font-weight:500;">% of Total</th>
          <th style="padding:7px 14px;text-align:left;color:#475569;font-size:.72rem;font-weight:500;">Impressions</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>"""


def _region_html(rows: list[dict]) -> str:
    spend: dict[str, float] = {}
    for r in rows:
        reg = r.get("region", "Unknown")
        spend[reg] = spend.get(reg, 0) + float(r.get("spend", 0) or 0)

    total = sum(spend.values()) or 1
    top   = sorted(spend.items(), key=lambda x: -x[1])[:20]

    if not top:
        return '<p style="color:#475569;font-style:italic;margin-top:8px;">No US state data for this campaign.</p>'

    html = '<p style="color:#64748b;font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;margin:20px 0 10px;">US States (Top 20 by Spend)</p>'
    html += '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;">'
    for i, (reg, s) in enumerate(top):
        col = "#60a5fa" if i < 5 else "#818cf8" if i < 10 else "#a78bfa" if i < 15 else "#94a3b8"
        html += f"""
        <div style="background:#12151f;border:1px solid #2a2e3a;border-radius:6px;padding:10px 12px;">
          <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:5px;">
            <span style="color:#e4e7ef;font-size:.82rem;font-weight:500;">{reg}</span>
            <span style="color:{col};font-size:.82rem;font-weight:700;">{_pct(s,total)}</span>
          </div>
          {_bar(_pct(s, total), col)}
          <div style="color:#475569;font-size:.72rem;margin-top:4px;">${s:,.2f}</div>
        </div>"""
    html += '</div>'
    return html


# ═══════════════════════════════════════════════════════════════════════════
# 3. HTML report assembly
# ═══════════════════════════════════════════════════════════════════════════

def _audience_narrative(t: dict, campaign_name: str) -> str:
    """Build a plain-English paragraph describing who this campaign targets."""
    if not t:
        return ""

    parts = []

    # Who (gender + age)
    genders    = t.get("genders") or []
    age_range  = t.get("age_range") or ""
    who_parts  = []
    if genders and len(genders) < 2:
        who_parts.append(genders[0] + "s")
    else:
        who_parts.append("all genders")
    if age_range:
        who_parts.append(f"aged {age_range}")
    if who_parts:
        parts.append("Targets " + " ".join(who_parts))

    # Where
    locs = t.get("locations") or []
    if locs:
        loc_str = ", ".join(locs[:5]) + (" and more" if len(locs) > 5 else "")
        parts.append(f"in {loc_str}")

    sentence1 = (" ".join(parts) + ".") if parts else ""

    # Interests / behaviors
    interests = t.get("interests") or []
    behaviors = t.get("behaviors") or []
    combined  = interests + behaviors

    sentence2 = ""
    if t.get("advantage_plus"):
        sentence2 = ("Audience selection is fully automated via Meta Advantage+ — "
                     "no interest or behavior restrictions are applied; Meta's algorithm "
                     "finds the most likely converters.")
    elif combined:
        shown = combined[:8]
        rest  = len(combined) - len(shown)
        names = ", ".join(f'"{n}"' for n in shown)
        tail  = f" and {rest} more" if rest else ""
        sentence2 = f"Defined by interests and behaviors including {names}{tail}."

    # Custom / lookalike audiences
    custom    = t.get("custom_auds") or []
    sentence3 = ""
    if custom:
        shown  = custom[:5]
        rest   = len(custom) - len(shown)
        names  = "; ".join(shown)
        tail   = f" (+{rest} more)" if rest else ""
        sentence3 = f"Also layered with custom/lookalike audiences: {names}{tail}."

    # Exclusions
    excl      = t.get("exclusions") or []
    sentence4 = ""
    if excl:
        shown  = excl[:4]
        rest   = len(excl) - len(shown)
        names  = ", ".join(f'"{n}"' for n in shown)
        tail   = f" and {rest} more" if rest else ""
        sentence4 = f"Excluding: {names}{tail}."

    full = " ".join(s for s in [sentence1, sentence2, sentence3, sentence4] if s)
    return full or "No targeting description available."


def _targeting_html(t: dict) -> str:
    if not t:
        return '<p style="color:#475569;font-style:italic;">No targeting data available.</p>'

    COLORS = {
        "interests":   ("#60a5fa", "#1e3a5f"),
        "behaviors":   ("#f59e0b", "#3d2e0a"),
        "custom_auds": ("#a78bfa", "#2e1f5e"),
        "exclusions":  ("#f87171", "#3d1010"),
        "locations":   ("#34d399", "#0d3326"),
        "age":         ("#fb923c", "#3d1f0a"),
        "genders":     ("#f472b6", "#3d0f25"),
    }

    def _chips(items: list, key: str) -> str:
        if not items:
            return ""
        col, bg = COLORS[key]
        chips = "".join(
            f'<span style="background:{bg};color:{col};border:1px solid {col}44;'
            f'border-radius:20px;padding:4px 13px;font-size:.8rem;white-space:nowrap;">{item}</span>'
            for item in items
        )
        return f'<div style="display:flex;flex-wrap:wrap;gap:7px;margin-top:8px;">{chips}</div>'

    def _row(label: str, content: str) -> str:
        return f"""
        <div style="margin-bottom:18px;">
          <span style="color:#475569;font-size:.7rem;text-transform:uppercase;
                       letter-spacing:.08em;display:block;margin-bottom:2px;">{label}</span>
          {content}
        </div>"""

    html = ""

    if t.get("advantage_plus"):
        html += """
        <div style="background:#1e1f35;border:1px solid #3730a3;border-radius:8px;
                    padding:14px 18px;margin-bottom:18px;">
          <span style="color:#818cf8;font-weight:600;font-size:.85rem;">Advantage+ Audience (AI-driven)</span>
          <p style="color:#64748b;font-size:.82rem;margin:6px 0 0;line-height:1.6;">
            No explicit interest or behavior restrictions. Meta's algorithm automatically
            finds the most likely converters based on pixel data and lookalikes.
          </p>
        </div>"""

    # Age & Gender
    age   = t.get("age_range") or ""
    gens  = t.get("genders") or []
    if age:
        html += _row("Target Age", _chips([age], "age"))
    if gens:
        html += _row("Target Gender", _chips(gens, "genders"))

    # Locations
    locs = t.get("locations") or []
    if locs:
        html += _row(f"Target Locations ({len(locs)})", _chips(locs, "locations"))

    # Interests
    interests = t.get("interests") or []
    if interests:
        html += _row(f"Interests ({len(interests)})", _chips(interests, "interests"))

    # Behaviors
    behaviors = t.get("behaviors") or []
    if behaviors:
        html += _row(f"Behaviors ({len(behaviors)})", _chips(behaviors, "behaviors"))

    # Custom / Lookalike Audiences
    custom = t.get("custom_auds") or []
    if custom:
        html += _row(f"Custom &amp; Lookalike Audiences ({len(custom)})", _chips(custom, "custom_auds"))

    # Exclusions
    excl = t.get("exclusions") or []
    if excl:
        html += _row(f"Excluded ({len(excl)})", _chips(excl, "exclusions"))

    if not html:
        html = '<p style="color:#475569;font-style:italic;">No explicit targeting configured.</p>'

    return html


def _color_for(keyword: str) -> str:
    for k, c in CAMPAIGN_COLORS.items():
        if k.lower() in keyword.lower():
            return c
    return "#60a5fa"


def build_report(demo_data: dict) -> str:
    sections = ""
    for cid, d in demo_data.items():
        col  = _color_for(d["keyword"])
        ctr  = (d["clicks_30d"] / d["impr_30d"] * 100) if d["impr_30d"] else 0

        sections += f"""
  <div style="background:#0d1018;border:1px solid {col}33;border-radius:14px;
              margin-bottom:36px;overflow:hidden;">

    <!-- Campaign header -->
    <div style="background:linear-gradient(120deg,{col}18 0%,transparent 55%);
                padding:26px 30px;border-bottom:1px solid #1e2235;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:16px;">
        <div>
          <div style="color:{col};font-size:.68rem;text-transform:uppercase;letter-spacing:.12em;margin-bottom:6px;">{d['keyword']}</div>
          <h2 style="margin:0;color:#fff;font-size:1.25rem;font-weight:700;">{d['name']}</h2>
          <div style="color:#334155;font-size:.75rem;margin-top:4px;">ID: {cid}</div>
        </div>
        <div style="display:flex;gap:20px;flex-wrap:wrap;">
          <div>
            <div style="color:#475569;font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;">30d Spend</div>
            <div style="color:{col};font-size:1.5rem;font-weight:700;margin-top:2px;">${d['spend_30d']:,.2f}</div>
          </div>
          <div>
            <div style="color:#475569;font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;">Impressions</div>
            <div style="color:#e4e7ef;font-size:1.5rem;font-weight:700;margin-top:2px;">{d['impr_30d']:,}</div>
          </div>
          <div>
            <div style="color:#475569;font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;">Clicks</div>
            <div style="color:#e4e7ef;font-size:1.5rem;font-weight:700;margin-top:2px;">{d['clicks_30d']:,}</div>
          </div>
          <div>
            <div style="color:#475569;font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;">CTR</div>
            <div style="color:#e4e7ef;font-size:1.5rem;font-weight:700;margin-top:2px;">{ctr:.2f}%</div>
          </div>
        </div>
      </div>
    </div>

    <div style="padding:26px 30px;">

      <!-- Audience Profile overview — top of campaign -->
      <div style="background:linear-gradient(135deg,{col}10 0%,#13161f 70%);
                  border:1px solid {col}30;border-radius:10px;
                  padding:20px 22px;margin-bottom:28px;">
        <div style="color:{col};font-size:.68rem;text-transform:uppercase;
                    letter-spacing:.12em;margin-bottom:10px;font-weight:600;">Audience Overview</div>
        <p style="color:#cbd5e1;font-size:.93rem;line-height:1.8;margin:0 0 16px;">
          {_audience_narrative(d['targeting'], d['name'])}
        </p>
        <!-- mini chips summary row -->
        <div style="display:flex;flex-wrap:wrap;gap:8px;padding-top:14px;
                    border-top:1px solid {col}20;">
          {"".join(
            f'<span style="background:{col}15;color:{col};border:1px solid {col}30;'
            f'border-radius:20px;padding:3px 12px;font-size:.76rem;">{item}</span>'
            for item in (
              ([d["targeting"].get("age_range")] if d["targeting"].get("age_range") else [])
              + d["targeting"].get("genders", [])
              + d["targeting"].get("locations", [])
              + d["targeting"].get("interests", [])
              + d["targeting"].get("behaviors", [])
              + d["targeting"].get("custom_auds", [])
            )
          )}
        </div>
      </div>

      <!-- Gender & Age -->
      <div style="margin-bottom:36px;">
        <h3 style="color:#e4e7ef;font-size:.95rem;font-weight:600;margin:0 0 2px;">
          <span style="color:{col};">&#9679;</span>&nbsp; Gender &amp; Age Demographics
        </h3>
        <p style="color:#475569;font-size:.78rem;margin:0;">Spend-weighted delivery, last 30 days.</p>
        {_gender_age_html(d['gender_age'])}
      </div>

      <div style="border-top:1px solid #1e2235;padding-top:28px;margin-bottom:36px;">
        <h3 style="color:#e4e7ef;font-size:.95rem;font-weight:600;margin:0 0 2px;">
          <span style="color:{col};">&#9679;</span>&nbsp; Location Breakdown
        </h3>
        <p style="color:#475569;font-size:.78rem;margin:0 0 16px;">Country-level distribution. US traffic broken down by state below.</p>
        {_country_html(d['country'])}
        {_region_html(d['region_us'])}
      </div>

      <!-- Interests & Audience Segments -->
      <div style="border-top:1px solid #1e2235;padding-top:28px;margin-bottom:36px;">
        <h3 style="color:#e4e7ef;font-size:.95rem;font-weight:600;margin:0 0 2px;">
          <span style="color:{col};">&#9679;</span>&nbsp; Interests &amp; Audience Segments
        </h3>
        <p style="color:#475569;font-size:.78rem;margin:0 0 16px;">
          Full targeting configuration across all {d['targeting'].get('adset_count', '?')} ad sets.
        </p>
        {_targeting_html(d['targeting'])}
      </div>

      <!-- Income note -->
      <div style="background:#1a1d27;border-left:3px solid #f59e0b;border-radius:0 8px 8px 0;padding:14px 18px;">
        <span style="color:#f59e0b;font-weight:600;font-size:.85rem;">Income Data — Not Available</span>
        <p style="color:#64748b;font-size:.82rem;margin:6px 0 0;line-height:1.6;">
          Meta does not provide income demographic reporting anywhere — not via API, not in Ads Manager.
          Income exists only as a <strong style="color:#94a3b8;">targeting option</strong> when setting up an ad set
          (Ad Set &rarr; Audience &rarr; Demographics &rarr; Financial &rarr; Income), but Meta
          does not report back what income brackets actually saw or engaged with your ads.
        </p>
      </div>

    </div>
  </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Audience Demographics Report</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#080b12;color:#e4e7ef;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;line-height:1.5}}
    table{{border-collapse:collapse;width:100%}}
    tr:hover td{{background:rgba(255,255,255,.02)}}
  </style>
</head>
<body>
  <div style="max-width:1080px;margin:0 auto;padding:44px 24px;">

    <div style="margin-bottom:44px;padding-bottom:24px;border-bottom:1px solid #1e2235;">
      <h1 style="font-size:1.9rem;font-weight:800;color:#fff;letter-spacing:-.02em;">
        Audience Demographics Report
      </h1>
      <p style="color:#475569;margin-top:6px;font-size:.88rem;">
        Meta Ads &nbsp;&middot;&nbsp; Last 30 days ({SINCE} to {UNTIL}) &nbsp;&middot;&nbsp; Generated {TODAY.isoformat()}
      </p>
      <div style="margin-top:16px;background:#111827;border:1px solid #1e2235;border-radius:8px;
                  padding:13px 18px;display:flex;gap:10px;align-items:flex-start;">
        <span style="color:#f59e0b;font-size:1rem;margin-top:1px;">&#9888;</span>
        <p style="color:#64748b;font-size:.82rem;line-height:1.6;margin:0;">
          <strong style="color:#94a3b8;">Income data is not available</strong> — Meta does not report
          income demographics anywhere (not via API, not in Ads Manager). It exists only as a targeting
          input, not as a reporting dimension. All other breakdowns below are sourced live from the Meta Insights API.
        </p>
      </div>
    </div>

    {sections}

    <footer style="text-align:center;color:#1e2235;font-size:.72rem;padding:20px 0 0;">
      Nowa Campaigns Dashboard &middot; {TODAY.isoformat()}
    </footer>
  </div>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════
# 4. Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    settings = load_settings()
    print(f"Date range: {SINCE} to {UNTIL}\n")
    demo_data = pull_all(settings)
    print("\nBuilding HTML ...")
    html = build_report(demo_data)
    out = ROOT / "reports" / f"demographics_{TODAY.isoformat()}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Report saved: {out}  ({len(html)//1024} KB)")


if __name__ == "__main__":
    main()
