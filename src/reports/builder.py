"""HTML report assembly for daily and weekly Telegram messages.

REPORT-02: Daily digest — TL;DR (top), total spend, ROAS, top/bottom campaigns, pacing.
REPORT-03: Weekly summary — WoW comparisons for all Tier-1 metrics + AI narrative.
REPORT-04: HTML formatting with <b> headers, emoji status indicators, ParseMode.HTML.

CLAUDE.md: html.escape() on every dynamic string before interpolation.
CLAUDE.md: meta_ prefix for Meta conversion fields (meta_purchases_7dclick, meta_cost_per_purchase).
"""
from __future__ import annotations

import html
from datetime import date, timedelta


def _roas_emoji(roas: float) -> str:
    """Return emoji status indicator for ROAS value. D-11."""
    if roas >= 2.0:
        return "🟢"
    if roas < 1.0:
        return "🔴"
    return "⚠️"


def _fmt_spend(amount: float) -> str:
    return f"${amount:,.2f}"


def _fmt_delta(this_val: float, last_val: float | None, unit: str = "$") -> str:
    """Format WoW delta as 'last → this (+abs / +pct)' per CONTEXT.md SPECIFICS."""
    if last_val is None or last_val == 0:
        return f"{unit}{this_val:,.2f} (no prior week data)"
    delta_abs = this_val - last_val
    delta_pct = (delta_abs / last_val) * 100
    sign = "+" if delta_abs >= 0 else ""
    if unit == "$":
        return (
            f"{unit}{last_val:,.2f} → {unit}{this_val:,.2f} "
            f"({sign}{unit}{abs(delta_abs):,.2f} / {sign}{delta_pct:.1f}%)"
        )
    return (
        f"{last_val:.2f} → {this_val:.2f} "
        f"({sign}{abs(delta_abs):.2f} / {sign}{delta_pct:.1f}%)"
    )


def build_daily_report_html(
    rows: list[dict],
    tldr: str | None,
    date_str: str,
) -> str:
    """Assemble the daily digest HTML message.

    Structure (TL;DR first, then metrics):
      1. Header
      2. TL;DR (or unavailable notice)
      3. Overall metrics (spend, ROAS, purchases)
      4. Top 3 campaigns by ROAS
      5. Bottom 3 campaigns by ROAS (min spend threshold applied)
      6. Spend pacing notice

    D-09: html.escape() on every dynamic string.
    D-12: Caller must pass this to split_html_message() before sending.
    """
    safe_date = html.escape(date_str)
    parts: list[str] = []

    parts.append(f"<b>📊 Daily Meta Ads Report — {safe_date}</b>")
    parts.append("")

    if tldr:
        parts.append("<b>TL;DR</b>")
        parts.append(html.escape(tldr))
        parts.append("")
    else:
        parts.append("<i>(TL;DR unavailable — Anthropic API error)</i>")
        parts.append("")

    if not rows:
        parts.append("<i>⚠️ No data available for this date. Check Meta ingest job.</i>")
        return "\n".join(parts)

    total_spend = sum(r.get("spend", 0) or 0 for r in rows)
    total_purchases = sum(r.get("meta_purchases_7dclick", 0) or 0 for r in rows)
    spend_weighted_roas = (
        sum((r.get("roas", 0) or 0) * (r.get("spend", 0) or 0) for r in rows) / total_spend
        if total_spend > 0 else 0.0
    )
    parts.append("<b>Overall</b>")
    parts.append(f"Spend: {_fmt_spend(total_spend)}")
    parts.append(f"ROAS: {spend_weighted_roas:.2f} {_roas_emoji(spend_weighted_roas)}")
    parts.append(f"Purchases: {total_purchases:,}")
    parts.append("")

    with_spend = [r for r in rows if (r.get("spend") or 0) > 1]
    top3 = sorted(with_spend, key=lambda r: r.get("roas") or 0, reverse=True)[:3]
    if top3:
        parts.append("<b>Top 3 by ROAS</b>")
        for r in top3:
            name = html.escape(str(r.get("campaign_name", r.get("campaign_id", ""))))
            roas = r.get("roas") or 0
            spend = r.get("spend") or 0
            parts.append(f"{_roas_emoji(roas)} {name}: ROAS {roas:.2f} | Spend {_fmt_spend(spend)}")
        parts.append("")

    bottom3 = sorted(with_spend, key=lambda r: r.get("roas") or 0)[:3]
    if bottom3:
        parts.append("<b>Bottom 3 by ROAS</b>")
        for r in bottom3:
            name = html.escape(str(r.get("campaign_name", r.get("campaign_id", ""))))
            roas = r.get("roas") or 0
            spend = r.get("spend") or 0
            parts.append(f"{_roas_emoji(roas)} {name}: ROAS {roas:.2f} | Spend {_fmt_spend(spend)}")
        parts.append("")

    parts.append("<i>Budget pacing alerts fire separately when thresholds are breached.</i>")

    return "\n".join(parts)


def build_weekly_report_html(
    this_week_rows: list[dict],
    last_week_rows: list[dict],
    tldr: str | None,
    week_end_date: str,
) -> str:
    """Assemble the weekly summary HTML message with WoW comparisons.

    REPORT-03: WoW comparisons for all Tier-1 metrics + AI narrative.
    Delta format: "Spend: $1,200 → $1,450 (+$250 / +21%)" per CONTEXT.md SPECIFICS.

    D-09: html.escape() on every dynamic string.
    """
    safe_date = html.escape(week_end_date)
    parts: list[str] = []

    parts.append(f"<b>📅 Weekly Meta Ads Summary — week ending {safe_date}</b>")
    parts.append("")

    if tldr:
        parts.append("<b>TL;DR</b>")
        parts.append(html.escape(tldr))
        parts.append("")
    else:
        parts.append("<i>(TL;DR unavailable — Anthropic API error)</i>")
        parts.append("")

    if not this_week_rows:
        parts.append("<i>⚠️ No data available for this week. Check Meta ingest job.</i>")
        return "\n".join(parts)

    tw_spend = sum(r.get("spend", 0) or 0 for r in this_week_rows)
    tw_purchases = sum(r.get("meta_purchases_7dclick", 0) or 0 for r in this_week_rows)
    tw_clicks = sum(r.get("clicks", 0) or 0 for r in this_week_rows)
    tw_roas = (
        sum((r.get("roas", 0) or 0) * (r.get("spend", 0) or 0) for r in this_week_rows) / tw_spend
        if tw_spend > 0 else 0.0
    )
    tw_cpc = tw_spend / tw_clicks if tw_clicks > 0 else 0.0

    lw_spend = sum(r.get("spend", 0) or 0 for r in last_week_rows) if last_week_rows else None
    lw_purchases = sum(r.get("meta_purchases_7dclick", 0) or 0 for r in last_week_rows) if last_week_rows else None
    lw_roas_val: float | None = None
    if last_week_rows:
        lw_spend_sum = sum(r.get("spend", 0) or 0 for r in last_week_rows)
        lw_roas_val = (
            sum((r.get("roas", 0) or 0) * (r.get("spend", 0) or 0) for r in last_week_rows) / lw_spend_sum
            if lw_spend_sum > 0 else 0.0
        )
    lw_clicks = sum(r.get("clicks", 0) or 0 for r in last_week_rows) if last_week_rows else None
    lw_cpc: float | None = None
    if lw_spend is not None and lw_clicks:
        lw_cpc = lw_spend / lw_clicks

    parts.append("<b>Week-over-Week Metrics</b>")
    parts.append(f"Spend: {_fmt_delta(tw_spend, lw_spend)}")
    parts.append(f"ROAS: {_fmt_delta(tw_roas, lw_roas_val, unit='')}")
    parts.append(f"Purchases: {_fmt_delta(float(tw_purchases), float(lw_purchases) if lw_purchases is not None else None, unit='')}")
    parts.append(f"CPC: {_fmt_delta(tw_cpc, lw_cpc)}")
    parts.append("")

    by_campaign: dict[str, dict] = {}
    for r in this_week_rows:
        cid = r.get("campaign_id", "")
        if cid not in by_campaign:
            by_campaign[cid] = {"campaign_name": r.get("campaign_name", cid), "spend": 0.0, "roas_num": 0.0, "roas_den": 0.0}
        by_campaign[cid]["spend"] += r.get("spend", 0) or 0
        by_campaign[cid]["roas_num"] += (r.get("roas", 0) or 0) * (r.get("spend", 0) or 0)
        by_campaign[cid]["roas_den"] += r.get("spend", 0) or 0

    top5 = sorted(by_campaign.values(), key=lambda x: x["spend"], reverse=True)[:5]
    if top5:
        parts.append("<b>Top 5 Campaigns (this week)</b>")
        for c in top5:
            name = html.escape(str(c["campaign_name"]))
            spend = c["spend"]
            roas = c["roas_num"] / c["roas_den"] if c["roas_den"] > 0 else 0.0
            parts.append(f"{_roas_emoji(roas)} {name}: {_fmt_spend(spend)} | ROAS {roas:.2f}")
        parts.append("")

    return "\n".join(parts)


def get_wow_date_ranges(report_date: date) -> dict[str, str]:
    """Compute ISO date strings for WoW comparison windows.

    report_date: the Monday on which the weekly report fires.
    Returns dict with week_start, week_end, prev_week_start, prev_week_end keys.
    """
    week_end = report_date - timedelta(days=1)
    week_start = week_end - timedelta(days=6)
    prev_week_end = week_start - timedelta(days=1)
    prev_week_start = prev_week_end - timedelta(days=6)
    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "prev_week_start": prev_week_start.isoformat(),
        "prev_week_end": prev_week_end.isoformat(),
    }
