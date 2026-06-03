"""Meta Change Log Impact Report.

Correlates every significant change event in ad_changelogs with campaign performance
before vs. after the change date, ranks by absolute impact, and outputs HTML.

Analysis window:
  Before: up to 3 days preceding the change date
  After:  up to 3 days following the change date
  Minimum: 1 day of data on each side (skips edges where data is absent)

Metrics scored:
  FSD (form_submit_deposit)  weight 3  — primary KPI
  CTR                        weight 2
  CPC                        weight 2  (lower = better, inverted)
  ROAS                       weight 2
  CPM                        weight 1

Run:
    python -X utf8 scripts/changelog_impact_report.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_settings  # noqa: E402

DB_PATH = ROOT / "data" / "metrics.db"
TODAY = date.today()

TARGET_KEYWORDS = {
    "Nostalgia Bridge": "#f59e0b",
    "Sturdy Parenting": "#34d399",
    "Routine":          "#f472b6",
}

# Only these event types are meaningful at campaign analysis level
IMPORTANT_EVENTS = {
    "update_campaign_run_status":        ("Campaign status changed",      "status",   "CAMPAIGN"),
    "update_ad_set_run_status":          ("Ad set status changed",        "status",   "AD_SET"),
    "update_ad_run_status":              ("Ad status changed",            "status",   "AD"),
    "update_ad_creative":                ("Ad creative updated",          "creative", "AD"),
    "update_ad_set_budget":              ("Budget updated",               "budget",   "AD_SET"),
    "update_campaign_budget":            ("Campaign budget updated",      "budget",   "CAMPAIGN"),
    "update_ad_set_bid_strategy":        ("Bid strategy changed",         "bid",      "AD_SET"),
    "update_ad_set_target_spec":         ("Targeting changed",            "targeting","AD_SET"),
    "update_campaign_name":              ("Campaign name updated",        "other",    "CAMPAIGN"),
    "create_campaign_group":             ("Campaign created",             "create",   "CAMPAIGN"),
    "create_ad_set":                     ("Ad set created",               "create",   "AD_SET"),
    "create_ad":                         ("Ad created",                   "create",   "AD"),
    "delete_ad":                         ("Ad deleted",                   "delete",   "AD"),
    "delete_ad_set":                     ("Ad set deleted",               "delete",   "AD_SET"),
    "update_ad_set_target_spec":         ("Targeting changed",            "targeting","AD_SET"),
}

METRIC_WEIGHTS = {
    "meta_form_submit_deposit": ("FSD",  3, +1),   # higher = better
    "ctr":                      ("CTR",  2, +1),   # higher = better
    "cpc":                      ("CPC",  2, -1),   # lower  = better
    "roas":                     ("ROAS", 2, +1),   # higher = better
    "cpm":                      ("CPM",  1, -1),   # lower  = better
}


# ─── DB helpers ──────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def load_campaign_map() -> dict[str, dict]:
    """Return {campaign_id: {name, keyword, color}} for the 3 target campaigns."""
    conn = _conn()
    cur  = conn.cursor()
    cur.execute("SELECT id, name FROM campaigns WHERE source='meta_ads'")
    result = {}
    for row in cur.fetchall():
        cid, cname = row["id"], row["name"]
        for kw, col in TARGET_KEYWORDS.items():
            if kw.lower() in cname.lower():
                result[cid] = {"name": cname, "keyword": kw, "color": col}
                break
    conn.close()
    return result


def load_daily_metrics(campaign_ids: list[str]) -> dict[str, dict[str, dict]]:
    """Return {campaign_id: {date_str: {metric: value}}}."""
    conn = _conn()
    cur  = conn.cursor()
    placeholders = ",".join("?" * len(campaign_ids))
    cur.execute(f"""
        SELECT campaign_id, date, spend, impressions, clicks, ctr, cpc, cpm,
               roas, meta_form_submit_deposit, meta_purchases_7dclick,
               meta_cost_per_purchase, reach, frequency
        FROM ad_metrics
        WHERE campaign_id IN ({placeholders}) AND ad_set_id = ''
        ORDER BY campaign_id, date
    """, campaign_ids)
    data: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in cur.fetchall():
        data[r["campaign_id"]][r["date"]] = dict(r)
    conn.close()
    return data


def load_changelogs(campaign_map: dict) -> list[dict]:
    """Load all changelogs that can be attributed to target campaigns."""
    conn  = _conn()
    cur   = conn.cursor()
    # Build keyword filters
    kw_filters = " OR ".join(["object_name LIKE ?"] * len(TARGET_KEYWORDS))
    params = [f"%{kw}%" for kw in TARGET_KEYWORDS]
    cur.execute(f"""
        SELECT change_time, object_id, object_name, object_type,
               event_type, changed_fields, old_value, new_value, actor_name
        FROM ad_changelogs
        WHERE ({kw_filters})
        ORDER BY change_time
    """, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    # Attribute each row to a campaign
    for row in rows:
        oname = row.get("object_name", "")
        for cid, meta in campaign_map.items():
            if meta["keyword"].lower() in oname.lower():
                row["_campaign_id"]  = cid
                row["_campaign_kw"]  = meta["keyword"]
                row["_color"]        = meta["color"]
                break
        row["_date"] = row["change_time"][:10]

    return rows


# ─── Impact scoring ──────────────────────────────────────────────────────────

def _avg_metrics(metrics_by_date: dict[str, dict], dates: list[str]) -> dict[str, float]:
    vals: dict[str, list] = defaultdict(list)
    for d in dates:
        row = metrics_by_date.get(d)
        if not row:
            continue
        for col in METRIC_WEIGHTS:
            v = row.get(col)
            if v is not None and v != 0:
                vals[col].append(float(v))
    return {col: (sum(v) / len(v)) for col, v in vals.items() if v}


def _pct_delta(before: float, after: float) -> float:
    if before == 0:
        return 0.0
    return (after - before) / before * 100


def compute_impact(change_date: str, metrics_by_date: dict[str, dict]) -> dict | None:
    """Compute before/after delta for a change on change_date. Returns None if insufficient data."""
    cdate = datetime.strptime(change_date, "%Y-%m-%d").date()

    before_dates = [(cdate - timedelta(days=i)).isoformat() for i in range(1, 4)]
    after_dates  = [(cdate + timedelta(days=i)).isoformat() for i in range(1, 4)]

    before_avgs = _avg_metrics(metrics_by_date, before_dates)
    after_avgs  = _avg_metrics(metrics_by_date, after_dates)

    # Need at least one day of data on each side
    if not before_avgs or not after_avgs:
        return None

    deltas: dict[str, float] = {}
    for col, (label, weight, direction) in METRIC_WEIGHTS.items():
        b = before_avgs.get(col)
        a = after_avgs.get(col)
        if b is None or a is None:
            continue
        pct = _pct_delta(b, a)
        deltas[col] = pct

    if not deltas:
        return None

    # Weighted impact score: positive = improvement, negative = worsening
    score = 0.0
    for col, (label, weight, direction) in METRIC_WEIGHTS.items():
        if col in deltas:
            score += deltas[col] * weight * direction

    return {
        "before": before_avgs,
        "after":  after_avgs,
        "deltas": deltas,
        "score":  score,
        "before_days": [d for d in before_dates if d in metrics_by_date],
        "after_days":  [d for d in after_dates  if d in metrics_by_date],
    }


# ─── Change grouping ─────────────────────────────────────────────────────────

def group_changes(changelogs: list[dict]) -> list[dict]:
    """Group changelog rows into distinct change events.

    Groups by: (campaign_id, date, event_category, actor).
    Within a group, collapse object names into a list.
    Keeps only important event types; skips noise (pure ad-level status floods from Meta).
    """
    groups: dict[tuple, dict] = {}

    for row in changelogs:
        et = row.get("event_type", "")
        if et not in IMPORTANT_EVENTS:
            continue

        cid   = row.get("_campaign_id")
        if not cid:
            continue

        label, category, level = IMPORTANT_EVENTS[et]
        actor = row.get("actor_name") or "Unknown"
        dkey  = row["_date"]

        # Separate Meta bulk ad status changes (noise) vs. meaningful changes:
        # If actor=Meta AND level=AD AND category=status → skip (pure algorithm churn)
        if actor == "Meta" and level == "AD" and category == "status":
            continue

        key = (cid, dkey, category, actor, level)
        if key not in groups:
            groups[key] = {
                "campaign_id":  cid,
                "campaign_kw":  row["_campaign_kw"],
                "color":        row["_color"],
                "date":         dkey,
                "event_type":   et,
                "category":     category,
                "level":        level,
                "label":        label,
                "actor":        actor,
                "objects":      [],
                "old_values":   [],
                "new_values":   [],
            }
        g = groups[key]
        obj_name = row.get("object_name", "")
        if obj_name and obj_name not in g["objects"]:
            g["objects"].append(obj_name)
        if row.get("old_value"):
            g["old_values"].append(row["old_value"])
        if row.get("new_value"):
            g["new_values"].append(row["new_value"])

    return list(groups.values())


# ─── HTML helpers ────────────────────────────────────────────────────────────

def _delta_badge(pct: float, direction: int = 1) -> str:
    """Render a coloured +/- badge. direction=+1 means higher is better."""
    improved = (pct * direction) > 0
    color    = "#34d399" if improved else "#f87171"
    arrow    = "▲" if pct > 0 else "▼"
    return (
        f'<span style="background:{color}18;color:{color};border:1px solid {color}44;'
        f'border-radius:4px;padding:2px 7px;font-size:.75rem;font-weight:700;">'
        f'{arrow} {abs(pct):.1f}%</span>'
    )


def _metric_row(label: str, before: float | None, after: float | None,
                pct: float | None, direction: int, fmt: str = ".2f") -> str:
    b_str = f"{before:{fmt}}" if before is not None else "—"
    a_str = f"{after:{fmt}}"  if after  is not None else "—"
    badge = _delta_badge(pct, direction) if pct is not None else ""
    return f"""
    <tr style="border-bottom:1px solid #1a1d27;">
      <td style="padding:7px 12px;color:#94a3b8;font-size:.8rem;">{label}</td>
      <td style="padding:7px 12px;color:#64748b;font-size:.8rem;">{b_str}</td>
      <td style="padding:7px 12px;color:#e4e7ef;font-size:.8rem;">{a_str}</td>
      <td style="padding:7px 12px;">{badge}</td>
    </tr>"""


def _score_bar(score: float) -> str:
    """Visual impact bar — green for positive, red for negative."""
    clamped = max(-100, min(100, score))
    if clamped >= 0:
        color = "#34d399"
        pct   = clamped
        left  = "50%"
        width = f"{pct / 2:.1f}%"
    else:
        color = "#f87171"
        pct   = abs(clamped)
        width = f"{pct / 2:.1f}%"
        left  = f"{50 - pct/2:.1f}%"
    return f"""
    <div style="position:relative;height:6px;background:#1e2235;border-radius:3px;margin-top:8px;">
      <div style="position:absolute;top:50%;transform:translateY(-50%);
                  left:50%;width:1px;height:10px;background:#2a2e3a;"></div>
      <div style="position:absolute;left:{left};width:{width};height:100%;
                  background:{color};border-radius:3px;"></div>
    </div>"""


def _actor_badge(actor: str) -> str:
    if actor == "Meta":
        return '<span style="background:#3730a322;color:#818cf8;border:1px solid #3730a344;border-radius:4px;padding:2px 8px;font-size:.72rem;">🤖 Meta</span>'
    return f'<span style="background:#0d3d2a;color:#34d399;border:1px solid #34d39944;border-radius:4px;padding:2px 8px;font-size:.72rem;">👤 {actor}</span>'


def _level_badge(level: str) -> str:
    colors = {"CAMPAIGN": "#f59e0b", "AD_SET": "#60a5fa", "AD": "#a78bfa"}
    c = colors.get(level, "#94a3b8")
    return f'<span style="color:{c};font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;">{level}</span>'


def _object_names_html(objects: list[str], max_show: int = 3) -> str:
    shown = objects[:max_show]
    rest  = len(objects) - len(shown)
    parts = [f'<span style="color:#64748b;font-size:.75rem;">{o}</span>' for o in shown]
    if rest:
        parts.append(f'<span style="color:#334155;font-size:.75rem;">+{rest} more</span>')
    return '<br>'.join(parts)


CATEGORY_ICONS = {
    "status":    "⏸",
    "creative":  "🎨",
    "budget":    "💰",
    "bid":       "📊",
    "targeting": "🎯",
    "create":    "✨",
    "delete":    "🗑",
    "other":     "📝",
}


def build_change_card(event: dict, impact: dict | None, idx: int) -> str:
    col   = event["color"]
    score = impact["score"] if impact else 0
    score_color = "#34d399" if score > 5 else "#f87171" if score < -5 else "#94a3b8"
    icon  = CATEGORY_ICONS.get(event["category"], "•")

    # Metrics table
    metrics_html = ""
    if impact:
        m = METRIC_WEIGHTS
        before, after, deltas = impact["before"], impact["after"], impact["deltas"]
        for col_key, (label, weight, direction) in m.items():
            b = before.get(col_key)
            a = after.get(col_key)
            pct = deltas.get(col_key)
            fmt = ".1f" if col_key == "meta_form_submit_deposit" else ".3f" if col_key in ("ctr","roas") else ".2f"
            metrics_html += _metric_row(label, b, a, pct, direction, fmt)

        window_note = (
            f'{len(impact["before_days"])}d before / {len(impact["after_days"])}d after'
        )
    else:
        window_note = "Insufficient data for before/after analysis"

    score_display = f"{score:+.0f}" if impact else "N/A"

    return f"""
<div style="background:#0d1018;border:1px solid {event['color']}33;border-radius:10px;
            margin-bottom:16px;overflow:hidden;">
  <div style="display:flex;align-items:stretch;">

    <!-- Left accent bar -->
    <div style="width:4px;background:{score_color};flex-shrink:0;"></div>

    <div style="flex:1;padding:16px 18px;">
      <!-- Header row -->
      <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
          <span style="font-size:1rem;">{icon}</span>
          <div>
            <span style="color:#e4e7ef;font-weight:600;font-size:.9rem;">{event['label']}</span>
            <span style="margin-left:8px;">{_level_badge(event['level'])}</span>
          </div>
          {_actor_badge(event['actor'])}
        </div>
        <div style="text-align:right;">
          <div style="color:{score_color};font-size:1.1rem;font-weight:700;">{score_display}</div>
          <div style="color:#334155;font-size:.68rem;">impact score</div>
          {_score_bar(score) if impact else ''}
        </div>
      </div>

      <!-- Meta info row -->
      <div style="display:flex;gap:16px;margin-bottom:10px;flex-wrap:wrap;">
        <span style="color:#475569;font-size:.75rem;">📅 {event['date']}</span>
        <span style="color:{event['color']};font-size:.75rem;font-weight:500;">{event['campaign_kw']}</span>
        <span style="color:#334155;font-size:.72rem;">{window_note}</span>
      </div>

      <!-- Object names -->
      <div style="margin-bottom:12px;">
        {_object_names_html(event['objects'])}
      </div>

      <!-- Before / After metrics -->
      {'<table style="width:100%;border-collapse:collapse;"><thead><tr style="border-bottom:1px solid #2a2e3a;"><th style="padding:5px 12px;text-align:left;color:#334155;font-size:.7rem;font-weight:500;">Metric</th><th style="padding:5px 12px;text-align:left;color:#334155;font-size:.7rem;font-weight:500;">Before</th><th style="padding:5px 12px;text-align:left;color:#334155;font-size:.7rem;font-weight:500;">After</th><th style="padding:5px 12px;text-align:left;color:#334155;font-size:.7rem;font-weight:500;">Change</th></tr></thead><tbody>' + metrics_html + '</tbody></table>' if impact else f'<p style="color:#334155;font-size:.78rem;font-style:italic;">{window_note}</p>'}
    </div>
  </div>
</div>"""


def build_report(events_with_impact: list[tuple[dict, dict | None]]) -> str:
    # Split into improved / worsened / neutral
    improved  = [(e, i) for e, i in events_with_impact if i and i["score"] >  5]
    worsened  = [(e, i) for e, i in events_with_impact if i and i["score"] < -5]
    neutral   = [(e, i) for e, i in events_with_impact if i and -5 <= i["score"] <= 5]
    no_data   = [(e, i) for e, i in events_with_impact if not i]

    improved.sort(key=lambda x: -x[1]["score"])
    worsened.sort(key=lambda x:  x[1]["score"])

    def _section(title: str, pairs: list, color: str) -> str:
        if not pairs:
            return f'<p style="color:#334155;font-style:italic;padding:16px 0;">No {title.lower()} events found.</p>'
        return "".join(build_change_card(e, i, idx) for idx, (e, i) in enumerate(pairs))

    # Summary stats
    total_you  = sum(1 for e, _ in events_with_impact if e["actor"] != "Meta")
    total_meta = sum(1 for e, _ in events_with_impact if e["actor"] == "Meta")
    top_win = improved[0]  if improved  else None
    top_loss= worsened[0]  if worsened  else None

    summary_cards = ""
    for label, val, col in [
        ("Total Changes Analysed", len(events_with_impact), "#60a5fa"),
        ("Your Changes",           total_you,               "#34d399"),
        ("Meta's Changes",         total_meta,              "#818cf8"),
        ("Improvements",           len(improved),           "#34d399"),
        ("Worsenings",             len(worsened),           "#f87171"),
    ]:
        summary_cards += f"""
        <div style="background:#0d1018;border:1px solid #1e2235;border-radius:8px;padding:14px 18px;text-align:center;">
          <div style="color:{col};font-size:1.6rem;font-weight:800;">{val}</div>
          <div style="color:#475569;font-size:.72rem;margin-top:3px;">{label}</div>
        </div>"""

    highlight_html = ""
    if top_win:
        e, i = top_win
        highlight_html += f"""
        <div style="background:#0d3326;border:1px solid #34d39933;border-radius:8px;padding:14px 18px;">
          <div style="color:#34d399;font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px;">Biggest Win</div>
          <div style="color:#e4e7ef;font-weight:600;">{e['label']} · {e['date']}</div>
          <div style="color:#64748b;font-size:.8rem;">{e['campaign_kw']} · {e['actor']} · score {i['score']:+.0f}</div>
        </div>"""
    if top_loss:
        e, i = top_loss
        highlight_html += f"""
        <div style="background:#3d1010;border:1px solid #f8717133;border-radius:8px;padding:14px 18px;">
          <div style="color:#f87171;font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px;">Biggest Drop</div>
          <div style="color:#e4e7ef;font-weight:600;">{e['label']} · {e['date']}</div>
          <div style="color:#64748b;font-size:.8rem;">{e['campaign_kw']} · {e['actor']} · score {i['score']:+.0f}</div>
        </div>"""

    improved_html = _section("Improved", improved, "#34d399")
    worsened_html = _section("Worsened", worsened, "#f87171")
    neutral_html  = _section("Neutral",  neutral,  "#94a3b8")

    no_data_html = ""
    if no_data:
        no_data_html = f"""
        <details style="margin-top:12px;">
          <summary style="color:#334155;font-size:.8rem;cursor:pointer;padding:8px 0;">
            {len(no_data)} changes without enough data to analyse (click to expand)
          </summary>
          <div style="margin-top:8px;">
            {"".join(build_change_card(e, None, i) for i, (e, _) in enumerate(no_data[:30]))}
          </div>
        </details>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Change Log Impact Report</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#080b12;color:#e4e7ef;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;line-height:1.5}}
    details summary{{list-style:none}}
    details summary::-webkit-details-marker{{display:none}}
  </style>
</head>
<body>
<div style="max-width:1000px;margin:0 auto;padding:44px 24px;">

  <!-- Header -->
  <div style="margin-bottom:36px;padding-bottom:24px;border-bottom:1px solid #1e2235;">
    <h1 style="font-size:1.9rem;font-weight:800;color:#fff;letter-spacing:-.02em;">Change Log Impact Report</h1>
    <p style="color:#475569;margin-top:6px;font-size:.88rem;">
      Meta Ads · Nostalgia Bridge Dad, Sturdy Parenting, Routine Chaos
      &nbsp;·&nbsp; Analysed {TODAY.isoformat()} &nbsp;·&nbsp; Metrics window May 13–28
    </p>
    <p style="color:#334155;font-size:.8rem;margin-top:6px;">
      Impact score = weighted delta across FSD (×3), CTR (×2), CPC (×2, inverted), ROAS (×2), CPM (×1, inverted).
      Positive score = performance improved after change. Minimum ±5 to count as meaningful.
    </p>
  </div>

  <!-- Summary KPIs -->
  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:28px;">
    {summary_cards}
  </div>

  <!-- Highlights -->
  {'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:36px;">' + highlight_html + '</div>' if highlight_html else ''}

  <!-- Tabs-style sections -->
  <div style="margin-bottom:36px;">
    <h2 style="color:#34d399;font-size:1rem;font-weight:700;margin-bottom:16px;
               padding-bottom:8px;border-bottom:2px solid #34d39933;">
      ▲ Improved Performance ({len(improved)} events)
    </h2>
    {improved_html}
  </div>

  <div style="margin-bottom:36px;">
    <h2 style="color:#f87171;font-size:1rem;font-weight:700;margin-bottom:16px;
               padding-bottom:8px;border-bottom:2px solid #f8717133;">
      ▼ Worsened Performance ({len(worsened)} events)
    </h2>
    {worsened_html}
  </div>

  <div style="margin-bottom:36px;">
    <h2 style="color:#94a3b8;font-size:1rem;font-weight:700;margin-bottom:16px;
               padding-bottom:8px;border-bottom:2px solid #94a3b833;">
      → Neutral / No Significant Change ({len(neutral)} events)
    </h2>
    {neutral_html}
  </div>

  {no_data_html}

  <footer style="text-align:center;color:#1e2235;font-size:.72rem;padding:24px 0 0;">
    Nowa Campaigns Dashboard · {TODAY.isoformat()}
  </footer>
</div>
</body>
</html>"""


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    settings = load_settings()

    print("Loading campaign map ...")
    campaign_map = load_campaign_map()
    if not campaign_map:
        print("No target campaigns found in DB.")
        sys.exit(1)
    for cid, m in campaign_map.items():
        print(f"  {m['keyword']:20s} {cid}")

    print("Loading daily metrics ...")
    metrics = load_daily_metrics(list(campaign_map.keys()))
    for cid, days in metrics.items():
        print(f"  {campaign_map[cid]['keyword']:20s} {len(days)} days  ({min(days.keys())} to {max(days.keys())})")

    print("Loading changelogs ...")
    changelogs = load_changelogs(campaign_map)
    print(f"  {len(changelogs)} relevant changelog entries")

    print("Grouping into change events ...")
    events = group_changes(changelogs)
    print(f"  {len(events)} distinct change events")

    print("Computing before/after impact ...")
    results: list[tuple[dict, dict | None]] = []
    for event in events:
        cid    = event["campaign_id"]
        cdate  = event["date"]
        impact = compute_impact(cdate, metrics.get(cid, {}))
        results.append((event, impact))

    # Sort: improved first (desc), then worsened (asc), then neutral, then no-data
    def sort_key(pair):
        e, i = pair
        if i is None:       return (3,  0)
        if i["score"] >  5: return (0, -i["score"])
        if i["score"] < -5: return (1,  i["score"])
        return (2, 0)
    results.sort(key=sort_key)

    with_impact = sum(1 for _, i in results if i)
    print(f"  {with_impact} with analysable data, {len(results)-with_impact} skipped (edge of data window)")

    print("Building HTML ...")
    html = build_report(results)

    out = ROOT / "reports" / f"changelog_impact_{TODAY.isoformat()}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"\nReport saved: {out}  ({len(html)//1024} KB)")


if __name__ == "__main__":
    main()
