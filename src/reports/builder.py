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


def _build_ga4_daily_section(
    ga4_campaign_rows: list[dict],
    ga4_landing_rows: list[dict],
    meta_rows: list[dict],
    ga4_landing_7day_rows: list[dict] | None = None,
) -> list[str]:
    """Assemble the Website (GA4) section lines for the daily digest.

    D-01: Section header '--- Website (GA4) ---'.
    D-02: Side-by-side attribution comparison for UTM-matched campaigns.
    D-03: Top 3 landing pages by ga4_purchases_lastclick.
    D-04: 7-day rolling trend summary line below yesterday top 3.
    D-06: UTM coverage warning at bottom (only when unmatched > 0).
    D-07: Warning omitted entirely when all campaigns match.
    CLAUDE.md: html.escape() on all dynamic strings.
    """
    parts: list[str] = []
    parts.append("")
    parts.append("<b>--- Website (GA4) ---</b>")

    total_sessions = sum(int(r.get("sessions") or 0) for r in ga4_campaign_rows)
    parts.append(f"Sessions (yesterday): {total_sessions:,}")
    parts.append("")

    # Top 3 landing pages by conversions (D-03)
    if ga4_landing_rows:
        top3_lp = sorted(
            ga4_landing_rows,
            key=lambda r: int(r.get("ga4_purchases_lastclick") or 0),
            reverse=True,
        )[:3]
        parts.append("<b>Top 3 Landing Pages (yesterday)</b>")
        for i, lp in enumerate(top3_lp, 1):
            page = html.escape(str(lp.get("landing_page") or ""))
            conv = int(lp.get("ga4_purchases_lastclick") or 0)
            sess = int(lp.get("sessions") or 0)
            parts.append(f"<b>{i}. {page}</b> — {conv} conversions, {sess} sessions")
        parts.append("")

    # 7-day landing page trend summary (D-04)
    if ga4_landing_7day_rows:
        total_7d_sess = sum(int(r.get("sessions") or 0) for r in ga4_landing_7day_rows)
        total_7d_conv = sum(int(r.get("ga4_purchases_lastclick") or 0) for r in ga4_landing_7day_rows)
        avg_sessions = total_7d_sess / 7
        avg_convs = total_7d_conv / 7
        parts.append(f"📊 7-day avg: {avg_sessions:.0f} sessions/day, {avg_convs:.1f} conversions/day")
        parts.append("")

    # Attribution comparison for UTM-matched campaigns (D-02)
    ga4_by_utm: dict[str, dict] = {
        r.get("campaign_utm", ""): r
        for r in ga4_campaign_rows
        if r.get("campaign_utm")
    }
    meta_names = [r.get("campaign_name") or "" for r in meta_rows if r.get("campaign_name")]
    matched_pairs = [
        (name, ga4_by_utm[name])
        for name in meta_names
        if name in ga4_by_utm
    ]
    if matched_pairs:
        parts.append("<b>Attribution Comparison (UTM-matched campaigns)</b>")
        for campaign_name, ga4_row in matched_pairs[:5]:
            safe_name = html.escape(campaign_name)
            meta_row = next(
                (r for r in meta_rows if r.get("campaign_name") == campaign_name), {}
            )
            meta_val = int(meta_row.get("meta_purchases_7dclick") or 0)
            ga4_val = int(ga4_row.get("ga4_purchases_lastclick") or 0)
            parts.append(
                f"<b>{safe_name}</b> — Purchases: Meta 7d-click: {meta_val} | "
                f"GA4 last-click: {ga4_val}"
            )
        parts.append(
            "<i>(Attribution difference is normal — Meta counts across 7 days, "
            "GA4 uses last-click on conversion day.)</i>"
        )
        parts.append("")

    # UTM coverage warning at bottom of GA4 section (D-06, D-07)
    total_meta = len([n for n in meta_names if n])
    matched_count = len(matched_pairs)
    unmatched = total_meta - matched_count
    if unmatched > 0:
        parts.append(
            f"⚠️ UTM coverage: {matched_count}/{total_meta} campaigns matched to GA4. "
            f"{unmatched} campaigns have no website data "
            f"(UTM tags missing or inconsistent)."
        )

    return parts


def _build_ga4_weekly_section(
    ga4_this_week: list[dict],
    ga4_last_week: list[dict],
) -> list[str]:
    """Assemble GA4 WoW section for the weekly summary.

    D-05: WoW deltas for top 3 landing pages — sessions and conversions.
    """
    parts: list[str] = []
    parts.append("")
    parts.append("<b>--- Website (GA4) Week-over-Week ---</b>")

    if not ga4_this_week:
        parts.append("<i>No GA4 data for this week.</i>")
        return parts

    total_sessions_tw = sum(int(r.get("sessions") or 0) for r in ga4_this_week)
    total_sessions_lw = sum(int(r.get("sessions") or 0) for r in ga4_last_week) if ga4_last_week else None
    parts.append(f"Sessions: {_fmt_delta(float(total_sessions_tw), float(total_sessions_lw) if total_sessions_lw is not None else None, unit='')}")
    parts.append("")

    top3_tw = sorted(
        ga4_this_week,
        key=lambda r: int(r.get("ga4_purchases_lastclick") or 0),
        reverse=True,
    )[:3]
    lw_by_page: dict[str, dict] = {
        r.get("landing_page", ""): r for r in ga4_last_week if r.get("landing_page")
    } if ga4_last_week else {}

    if top3_tw:
        parts.append("<b>Top 3 Landing Pages WoW</b>")
        for lp in top3_tw:
            page = html.escape(str(lp.get("landing_page") or ""))
            curr_sess = int(lp.get("sessions") or 0)
            curr_conv = int(lp.get("ga4_purchases_lastclick") or 0)
            lw_row = lw_by_page.get(lp.get("landing_page", ""), {})
            prev_sess = int(lw_row.get("sessions") or 0) if lw_row else None
            prev_conv = int(lw_row.get("ga4_purchases_lastclick") or 0) if lw_row else None

            parts.append(f"<b>{page}</b>")
            parts.append(f"  Sessions: {_fmt_delta(float(curr_sess), float(prev_sess) if prev_sess is not None else None, unit='')}")
            parts.append(f"  Conversions: {_fmt_delta(float(curr_conv), float(prev_conv) if prev_conv is not None else None, unit='')}")

    return parts


def build_daily_report_html(
    rows: list[dict],
    tldr: str | None,
    date_str: str,
    ga4_campaign_rows: list[dict] | None = None,
    ga4_landing_rows: list[dict] | None = None,
    ga4_landing_7day_rows: list[dict] | None = None,
    meta_available: bool = True,
    ga4_available: bool = True,
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

    if not meta_available:
        parts.append("<b>⚠️ Meta Ads data unavailable for this date</b> (ingestion failed — check logs)")
    if not rows:
        parts.append("<i>⚠️ No data available for this date. Check Meta ingest job.</i>")
        if not ga4_available:
            parts.append("<b>⚠️ GA4 data unavailable for this date</b> (ingestion failed — check logs)")
        elif ga4_campaign_rows or ga4_landing_rows:
            parts.extend(
                _build_ga4_daily_section(
                    ga4_campaign_rows or [],
                    ga4_landing_rows or [],
                    [],
                    ga4_landing_7day_rows=ga4_landing_7day_rows,
                )
            )
        return "\n".join(parts)

    total_spend = sum(r.get("spend", 0) or 0 for r in rows)
    total_purchases = sum(r.get("meta_purchases_7dclick", 0) or 0 for r in rows)
    total_form_submit = sum(r.get("meta_form_submit_deposit", 0) or 0 for r in rows)
    spend_weighted_roas = (
        sum((r.get("roas", 0) or 0) * (r.get("spend", 0) or 0) for r in rows) / total_spend
        if total_spend > 0 else 0.0
    )
    parts.append("<b>Overall</b>")
    parts.append(f"Spend: {_fmt_spend(total_spend)}")
    parts.append(f"ROAS: {spend_weighted_roas:.2f} {_roas_emoji(spend_weighted_roas)}")
    parts.append(f"Purchases (Meta 7d-click): {total_purchases:,}")
    parts.append(f"Form Submit Deposit: {total_form_submit:,}")
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

    # GA4 section (D-01): appended after Meta content when GA4 data is available
    if not ga4_available:
        parts.append("<b>⚠️ GA4 data unavailable for this date</b> (ingestion failed — check logs)")
    elif ga4_campaign_rows or ga4_landing_rows:
        parts.extend(
            _build_ga4_daily_section(
                ga4_campaign_rows or [],
                ga4_landing_rows or [],
                rows,
                ga4_landing_7day_rows=ga4_landing_7day_rows,
            )
        )

    return "\n".join(parts)


def build_weekly_report_html(
    this_week_rows: list[dict],
    last_week_rows: list[dict],
    tldr: str | None,
    week_end_date: str,
    ga4_this_week: list[dict] | None = None,
    ga4_last_week: list[dict] | None = None,
    meta_available: bool = True,
    ga4_available: bool = True,
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

    if not meta_available:
        parts.append("<b>⚠️ Meta Ads data unavailable for this week</b> (ingestion failed — check logs)")
    if not this_week_rows:
        parts.append("<i>⚠️ No data available for this week. Check Meta ingest job.</i>")
        if not ga4_available:
            parts.append("<b>⚠️ GA4 data unavailable for this week</b> (ingestion failed — check logs)")
        elif ga4_this_week or ga4_last_week:
            parts.extend(
                _build_ga4_weekly_section(
                    ga4_this_week or [],
                    ga4_last_week or [],
                )
            )
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

    # GA4 WoW section (D-05)
    if not ga4_available:
        parts.append("<b>⚠️ GA4 data unavailable for this week</b> (ingestion failed — check logs)")
    elif ga4_this_week or ga4_last_week:
        parts.extend(
            _build_ga4_weekly_section(
                ga4_this_week or [],
                ga4_last_week or [],
            )
        )

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
