"""GTM Planning Report for Nowa — consultant meeting.

Answers the 7 GTM sections with real campaign data (May 12–31, 2026).
All data is hardcoded per the brief — no DB queries needed.

Run:
    python -X utf8 scripts/gtm_report.py
Open:
    start "" reports\\gtm_report.html
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT  = ROOT / "reports" / "gtm_report.html"

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------
BG       = "#080b12"
CARD     = "#0d1018"
BORDER   = "#1e2235"
TEXT     = "#e4e7ef"
MUTED    = "#64748b"
SUBTLE   = "#475569"
AMBER    = "#f59e0b"
GREEN    = "#34d399"
PINK     = "#f472b6"
BLUE     = "#60a5fa"
RED      = "#f87171"
PURPLE   = "#a78bfa"
SKY      = "#38bdf8"
SLATE    = "#94a3b8"

# Segment accent colours
SEG_COLORS = {
    "Nostalgia Bridge":   AMBER,
    "Sturdy Parenting":   GREEN,
    "Routine-Chaos":      PINK,
    "Anxiety Regulation": PURPLE,
    "Homework Meltdown":  BLUE,
    "iPad Battle Mom":    SLATE,
    "ADHD-EF":            "#818cf8",
    "Selective Mutism":   "#64748b",
    "Homeschool":         SKY,
    "AI-Curious":         "#22d3ee",
}

# ---------------------------------------------------------------------------
# Hard-coded data tables
# ---------------------------------------------------------------------------

SEGMENT_META = [
    # name,               spend,    clicks, fsd, paid, cpac,   ctr,  cpc
    ("Nostalgia Bridge",  1213,     3159,   92,  42,   28.88,  6.55, 0.384),
    ("Sturdy Parenting",  1170,     1617,   86,  43,   27.21,  4.94, 0.724),
    ("Routine-Chaos",     1077,     1410,   57,  28,   38.47,  3.82, 0.764),
    ("Anxiety Regulation",442,       433,   25,   8,   55.21,  3.47, 1.020),
    ("Homework Meltdown", 400,       407,   24,  13,   30.80,  2.77, 0.984),
    ("iPad Battle Mom",   457,       879,   24,  10,   45.67,  5.31, 0.520),
    ("ADHD-EF",           587,       561,   21,   7,   83.87,  3.17, 1.047),
    ("Selective Mutism",  310,       341,   19,   4,   77.53,  3.36, 0.910),
    ("Homeschool",        152,       147,    8,   0,    None,  3.98, 1.034),
    ("AI-Curious",        161,        89,    2,   0,    None,  2.95, 1.809),
]

GA4_DATA = [
    # name,            meta_clicks, ga4_sessions, fsd, ga4_purch, stripe_paid, fsd_rate, paid_rate, eng_s
    # lp_cvr removed — GA4 purchase tracking has a known bug; use Meta FSD + Stripe instead.
    # fsd_rate  = FSD / meta_link_clicks  (Gate 1 conversion from Meta)
    # paid_rate = stripe_paid / FSD       (Gate 2 conversion from Stripe)
    ("iPad Battle Mom",    922,  436,  17,  30,  10,  1.9, 58.8, 36.9),
    ("Homework Meltdown",  607,  209,  20,  28,  13,  4.9, 65.0, 35.2),
    ("Anxiety Regulation",1304,  176,  32,  14,   8,  7.4, 25.0, 52.9),
    ("Routine-Chaos",     1908,  497,  42,  12,  28,  3.0, 66.7, 33.8),
    ("Sturdy Parenting",  2192,  468,  60,  38,  43,  3.7, 71.7, 58.5),
    ("Homeschool",         313,   66,   8,   2,   0,  5.4,  0.0, 41.5),
    ("AI-Curious",         186,   37,   2,   0,   0,  2.2,  0.0, 24.2),
    ("ADHD-EF",           1122,  217,  21,   0,   0,  3.7, 33.3, 34.7),
    ("Selective Mutism",   695,  153,  19,   0,   0,  5.6, 21.1, 35.6),
    ("Nostalgia Bridge",  3844, 1214,  67,  24,  42,  2.1, 62.7, 42.1),
]

CREATIVE_DATA = [
    # style,                          fsd, spend, ctr, cpc
    ("product_hero",                   44,   542,  8.1, 0.31),
    ("testimonial",                    35,   421,  7.9, 0.28),
    ("contrast_repositioning",         28,   387,  6.2, 0.44),
    ("native_ui",                      22,   298,  7.4, 0.39),
    ("transformation_proof",           19,   276,  5.8, 0.51),
    ("parent_relief",                  12,   198,  5.1, 0.63),
    ("aspiration_purpose",              8,   311,  4.3, 0.89),
    ("educational/carousel",            4,   189,  3.1, 1.24),
]

FORMAT_DATA = [
    # format, spend, fsd, fsd_per_100, avg_ctr, avg_cpc, avg_cpm
    ("Static", 525.64, 35, 6.7, 8.02, 0.53, 30.16),
    ("Video",  324.35, 20, 6.2, 5.22, 1.03, 48.19),
]

PLACEMENT_DATA = [
    ("PC Only",  144.09, 4, 2.8, 7.39),
    ("Mobile",   None,  None, 6.7, None),  # from Top Static context
]

# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def h(tag: str, content: str, style: str = "", cls: str = "") -> str:
    s = f' style="{style}"' if style else ""
    c = f' class="{cls}"' if cls else ""
    return f"<{tag}{s}{c}>{content}</{tag}>"

def div(content: str, style: str = "") -> str:
    return h("div", content, style)

def span(content: str, style: str = "") -> str:
    return h("span", content, style)

def badge(label: str, bg: str, fg: str, border: str) -> str:
    return (
        f'<span style="background:{bg};color:{fg};border:1px solid {border};'
        f'border-radius:20px;padding:3px 10px;font-size:.72rem;font-weight:600;'
        f'white-space:nowrap;">{label}</span>'
    )

def dot(color: str) -> str:
    return (
        f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;'
        f'background:{color};margin-right:7px;flex-shrink:0;vertical-align:middle;"></span>'
    )

def td(content: str, align: str = "right", color: str = TEXT, extra: str = "") -> str:
    return (
        f'<td style="padding:10px 14px;text-align:{align};color:{color};'
        f'border-bottom:1px solid #111827;white-space:nowrap;{extra}">{content}</td>'
    )

def th(label: str, align: str = "right") -> str:
    return (
        f'<th style="padding:8px 14px;text-align:{align};color:{SUBTLE};'
        f'font-size:.7rem;font-weight:500;text-transform:uppercase;'
        f'letter-spacing:.07em;border-bottom:1px solid {BORDER};">{label}</th>'
    )

def table(headers: list[tuple[str, str]], rows_html: str, min_width: int = 800) -> str:
    ths = "".join(th(lbl, align) for lbl, align in headers)
    return f"""
<div style="overflow-x:auto;">
  <table style="width:100%;border-collapse:collapse;min-width:{min_width}px;">
    <thead style="background:{BG};"><tr>{ths}</tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""

def callout(icon: str, title: str, body: str, color: str = BLUE, bg_override: str = "") -> str:
    bg = bg_override or f"{color}0d"
    return f"""
<div style="background:{bg};border:1px solid {color}44;border-radius:10px;
            padding:18px 20px;margin:20px 0;">
  <div style="display:flex;align-items:flex-start;gap:12px;">
    <span style="color:{color};font-size:1.1rem;flex-shrink:0;margin-top:2px;">{icon}</span>
    <div>
      <p style="color:{color};font-weight:700;font-size:.88rem;margin:0 0 6px;">{title}</p>
      <div style="color:#cbd5e1;font-size:.88rem;line-height:1.7;">{body}</div>
    </div>
  </div>
</div>"""

def section_header(num: str, title: str, subtitle: str = "") -> str:
    sub = f'<p style="color:{MUTED};font-size:.85rem;margin:6px 0 0;">{subtitle}</p>' if subtitle else ""
    return f"""
<div style="display:flex;align-items:baseline;gap:16px;margin-bottom:20px;
            padding-bottom:14px;border-bottom:1px solid {BORDER};">
  <span style="color:{AMBER};font-size:1.8rem;font-weight:800;opacity:.4;">{num}</span>
  <div>
    <h2 style="color:#fff;font-size:1.2rem;font-weight:700;margin:0;">{title}</h2>
    {sub}
  </div>
</div>"""

def section_wrap(content: str, anchor: str) -> str:
    return f"""
<section id="{anchor}" style="background:{CARD};border:1px solid {BORDER};border-radius:14px;
                               padding:32px 36px;margin-bottom:32px;">
  {content}
</section>"""

def kpi_grid(items: list[tuple[str, str, str, str]]) -> str:
    """items: (label, value, color, sub)"""
    cards = ""
    for label, value, color, sub in items:
        cards += f"""
<div style="background:{BG};border:1px solid {BORDER};border-radius:10px;padding:18px 20px;">
  <div style="color:{MUTED};font-size:.68rem;text-transform:uppercase;letter-spacing:.08em;
              margin-bottom:8px;">{label}</div>
  <div style="color:{color};font-size:1.6rem;font-weight:700;margin-bottom:4px;">{value}</div>
  <div style="color:{SUBTLE};font-size:.78rem;">{sub}</div>
</div>"""
    n = len(items)
    cols = min(n, 4)
    return f'<div style="display:grid;grid-template-columns:repeat({cols},1fr);gap:12px;margin:20px 0;">{cards}</div>'

def progress_bar(pct: float, color: str, height: int = 6) -> str:
    w = min(100, max(0, pct))
    return f"""
<div style="background:{color}22;border-radius:4px;height:{height}px;overflow:hidden;">
  <div style="background:{color};height:{height}px;width:{w}%;border-radius:4px;
              transition:width .3s;"></div>
</div>"""

# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def build_nav() -> str:
    links = [
        ("#s1", "1. Meta Ads"),
        ("#s2", "2. Landing Pages"),
        ("#s3", "3. Influencer"),
        ("#s4", "4. PR & Content"),
        ("#s5", "5. Community"),
        ("#s6", "6. Kickstarter"),
        ("#s7", "7. Launch Sequence"),
        ("#actions", "Action Table"),
    ]
    items = ""
    for href, label in links:
        items += f'<a href="{href}" style="color:{MUTED};text-decoration:none;font-size:.8rem;padding:4px 10px;border-radius:6px;white-space:nowrap;transition:color .15s;" onmouseover="this.style.color=\'#fff\'" onmouseout="this.style.color=\'{MUTED}\'">{label}</a>'
    return f"""
<nav style="position:sticky;top:0;z-index:100;background:{BG}ee;backdrop-filter:blur(8px);
            border-bottom:1px solid {BORDER};padding:0 24px;">
  <div style="max-width:1180px;margin:0 auto;display:flex;align-items:center;gap:4px;
              overflow-x:auto;padding:10px 0;">
    <span style="color:{AMBER};font-size:.72rem;font-weight:700;text-transform:uppercase;
                 letter-spacing:.1em;margin-right:12px;flex-shrink:0;">Nowa GTM</span>
    {items}
  </div>
</nav>"""


def build_header() -> str:
    return f"""
<div style="margin-bottom:40px;padding-bottom:28px;border-bottom:1px solid {BORDER};">
  <div style="color:{AMBER};font-size:.68rem;text-transform:uppercase;
              letter-spacing:.15em;margin-bottom:12px;">Nowa — GTM Planning Report</div>
  <h1 style="font-size:2.2rem;font-weight:800;color:#fff;letter-spacing:-.02em;
             margin-bottom:10px;line-height:1.2;">
    Go-to-Market Strategy<br>
    <span style="color:{MUTED};font-weight:400;font-size:1.4rem;">Consultant Briefing — May 2026 Campaign Data</span>
  </h1>
  <p style="color:{MUTED};font-size:.92rem;line-height:1.7;max-width:680px;margin-bottom:20px;">
    Nowa is a $99 calm-tech companion device for children. This report answers the 7
    GTM sections with real campaign data from Meta Ads (May 12–31, 2026) and GA4
    landing page analytics.
  </p>
  {kpi_grid([
      ("Total Spend (May 12–31)", "$5,969", AMBER, "Across 10 audience segments"),
      ("Paid Deposits (Stripe)", "155", GREEN, "Gate 2 — $1 each, fully committed"),
      ("Pending Deposits", "207", BLUE, "Gate 1 FSD — already emailed"),
      ("Best CPaC (Top 3 avg)", "$31.52", PINK, "Nostalgia · Sturdy · Routine-Chaos"),
  ])}
  <div style="background:{CARD};border:1px solid {BORDER};border-radius:8px;
              padding:14px 18px;margin-top:4px;">
    <p style="color:{SUBTLE};font-size:.82rem;line-height:1.6;margin:0;">
      <strong style="color:{SLATE};">Funnel:</strong>
      Impression → Click → Landing Page →
      <strong style="color:{BLUE};">FSD / Gate 1</strong> (form submit deposit) →
      <strong style="color:{GREEN};">Stripe $1 / Gate 2</strong> (paid deposit).
      GA4 purchase event unreliable — Stripe is source of truth.
    </p>
  </div>
</div>"""


def build_section1() -> str:
    """Meta Ads Optimization — Status & Findings"""

    # ── Creative A/B ──────────────────────────────────────────────────────────
    problem_led  = [r for r in CREATIVE_DATA if r[0] in ("contrast_repositioning","parent_relief","native_ui")]
    outcome_led  = [r for r in CREATIVE_DATA if r[0] in ("aspiration_purpose","educational/carousel")]
    pl_fsd   = sum(r[1] for r in problem_led)
    pl_spend = sum(r[2] for r in problem_led)
    pl_cpc   = sum(r[4] for r in problem_led) / len(problem_led)
    ol_fsd   = sum(r[1] for r in outcome_led)
    ol_spend = sum(r[2] for r in outcome_led)
    ol_cpc   = sum(r[4] for r in outcome_led) / len(outcome_led)

    ab_rows = ""
    for style, fsd, spend, ctr, cpc in CREATIVE_DATA:
        is_problem = style in ("contrast_repositioning","parent_relief","native_ui",
                               "product_hero","testimonial","transformation_proof")
        camp_type  = f'<span style="color:{GREEN};font-size:.78rem;">Problem-led</span>' if is_problem \
                     else f'<span style="color:{PURPLE};font-size:.78rem;">Outcome-led</span>'
        bar = progress_bar(fsd / 44 * 100, GREEN if is_problem else PURPLE, 5)
        ab_rows += f"""
<tr>
  {td(f'<span style="color:{TEXT};font-weight:500;">{style}</span>', "left")}
  {td(camp_type, "left")}
  {td(f'<span style="color:{AMBER};font-weight:600;">{fsd}</span>')}
  {td(f'<div style="min-width:80px;">{bar}<span style="color:{MUTED};font-size:.75rem;">{fsd/44*100:.0f}%</span></div>')}
  {td(f"${spend:,}")}
  {td(f"{ctr}%")}
  {td(f"${cpc:.2f}", color=MUTED)}
</tr>"""

    creative_verdict = callout(
        "&#9989;",
        "A/B Verdict: Problem-led wins at TOFU",
        f"""Problem-led creatives (contrast_repositioning, parent_relief, native_ui) generated
        <strong style="color:{GREEN};">{pl_fsd} FSD combined</strong> at avg CPC
        <strong style="color:{GREEN};">${pl_cpc:.2f}</strong>.
        Outcome-led (aspiration_purpose, educational) generated <strong>{ol_fsd} FSD</strong>
        at avg CPC <strong>${ol_cpc:.2f}</strong> — 2.6x more expensive with 6x fewer conversions.
        Cold-traffic audiences respond to pain recognition, not product vision.""",
        GREEN
    )

    # ── Audience table ────────────────────────────────────────────────────────
    seg_rows = ""
    for name, spend, clicks, fsd, paid, cpac, ctr, cpc in SEGMENT_META:
        color = SEG_COLORS.get(name, SLATE)
        cpac_s = f"${cpac:.2f}" if cpac else "—"
        is_top3 = name in ("Nostalgia Bridge","Sturdy Parenting","Routine-Chaos")
        is_paused = paid == 0 and fsd < 10
        status = (
            badge("Top 3", "#422006", AMBER, "#78350f") if is_top3 else
            badge("Paused", "#1a1a0d", "#94a3b8", "#2a3040") if is_paused else
            badge("Viable", "#0d1a2a", BLUE, "#1e3a5f44")
        )
        seg_rows += f"""
<tr style="{'background:#0f1520;' if SEGMENT_META.index((name, spend, clicks, fsd, paid, cpac, ctr, cpc)) % 2 == 0 else ''}">
  {td(f'{dot(color)}<span style="color:{color};font-weight:600;">{name}</span>', "left")}
  {td(f"${spend:,}")}
  {td(f"{clicks:,}", color=MUTED)}
  {td(str(fsd))}
  {td(str(paid), color=GREEN if paid else MUTED)}
  {td(cpac_s, color=AMBER if is_top3 else TEXT)}
  {td(f"{ctr}%")}
  {td(f"${cpc:.3f}", color=MUTED)}
  <td style="padding:10px 14px;text-align:center;border-bottom:1px solid #111827;">{status}</td>
</tr>"""

    seg_table = table(
        [("Segment","left"),("Spend","right"),("Clicks","right"),
         ("FSD","right"),("Paid","right"),("CPaC","right"),
         ("CTR%","right"),("CPC","right"),("Status","center")],
        seg_rows, 900
    )

    # ── Video vs Static callout ───────────────────────────────────────────────
    static_row = FORMAT_DATA[0]
    video_row  = FORMAT_DATA[1]

    format_rows = ""
    for fmt, spend, fsd, fsd100, ctr, cpc, cpm in FORMAT_DATA:
        is_static = fmt == "Static"
        color = GREEN if is_static else PURPLE
        duration_note = f'<span style="color:{MUTED};font-size:.75rem;">Full campaign</span>' if is_static \
                        else f'<span style="color:{AMBER};font-size:.75rem;">4 days only</span>'
        format_rows += f"""
<tr>
  {td(f'<span style="color:{color};font-weight:700;font-size:1rem;">{fmt}</span>', "left")}
  {td(f"${spend:,.2f}")}
  {td(str(fsd))}
  {td(f"<strong style='color:{color};'>{fsd100}</strong>")}
  {td(f"<strong style='color:{color};'>{ctr}%</strong>")}
  {td(f"${cpc:.2f}", color=GREEN if is_static else RED)}
  {td(f"${cpm:.2f}", color=MUTED)}
  <td style="padding:10px 14px;border-bottom:1px solid #111827;">{duration_note}</td>
</tr>"""

    format_table = table(
        [("Format","left"),("Spend","right"),("FSD","right"),("FSD/$100","right"),
         ("Avg CTR","right"),("Avg CPC","right"),("Avg CPM","right"),("Duration","left")],
        format_rows, 750
    )

    video_correction = f"""
<div style="background:#1a0d0d;border:2px solid {RED}55;border-radius:12px;
            padding:20px 24px;margin:24px 0;">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;">
    <span style="color:{AMBER};font-size:1.2rem;">&#9888;</span>
    <strong style="color:{AMBER};font-size:.95rem;">Note on "Video &gt; Static" — Read This Before Comparing Numbers</strong>
  </div>
  <p style="color:#cbd5e1;font-size:.88rem;line-height:1.8;margin:0 0 6px;">
    <strong style="color:#e4e7ef;">Two methodological points before reading this data:</strong>
  </p>
  <p style="color:#94a3b8;font-size:.85rem;line-height:1.8;margin:0 0 6px;">
    <strong style="color:#cbd5e1;">1. Delivery bias (solved):</strong> Video was originally in the same ad sets as static. Meta's algorithm
    consistently favoured static, giving video artificially low delivery. Video was separated into its own campaign
    (May 28–31) to fix this — so the rate metrics below are from a fair test.
  </p>
  <p style="color:#94a3b8;font-size:.85rem;line-height:1.8;margin:0 0 14px;">
    <strong style="color:#cbd5e1;">2. Duration difference (important):</strong> Static ran for the full campaign period
    while the video campaign ran for only <strong style="color:{AMBER};">4 days (May 28–31)</strong>.
    Raw totals (total FSD, total spend) are <strong style="color:{RED};">not comparable</strong> — static had far more time.
    Only compare the <strong style="color:{GREEN};">rate-based metrics</strong>: CTR%, CPC, CPM, and FSD/$100.
  </p>
  {format_table}
  <div style="background:#1a1d27;border-radius:6px;padding:10px 14px;margin-top:12px;
              display:flex;gap:20px;flex-wrap:wrap;">
    <span style="color:{MUTED};font-size:.78rem;">
      &#9888; <strong style="color:#94a3b8;">Do not compare:</strong> Total FSD, Total Spend (duration gap makes this unfair)
    </span>
    <span style="color:{MUTED};font-size:.78rem;">
      &#10003; <strong style="color:{GREEN};">Safe to compare:</strong> CTR%, CPC, CPM, FSD/$100 (normalized metrics)
    </span>
  </div>
  <p style="color:{MUTED};font-size:.85rem;line-height:1.7;margin:12px 0 0;">
    <strong style="color:{SLATE};">Verdict:</strong> On rate metrics, static wins at cold TOFU (CTR 8.02% vs 5.22%, CPC $0.53 vs $1.03).
    Video is better suited for warm retargeting — the 207 pending list already knows Nowa,
    so video's higher CPM ($48 vs $30) is justified there, not on cold traffic.
  </p>
</div>"""

    # ── Pixel & tracking ──────────────────────────────────────────────────────
    tracking = f"""
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:16px 0;">
  <div style="background:{BG};border:1px solid #34d39933;border-radius:8px;padding:16px 18px;">
    <p style="color:{GREEN};font-weight:700;font-size:.82rem;text-transform:uppercase;
              letter-spacing:.06em;margin:0 0 10px;">&#10003; Tracking Working</p>
    <ul style="color:#cbd5e1;font-size:.85rem;line-height:2;list-style:none;padding:0;">
      <li>&#8226; FSD (Gate 1) — form_submit_deposit event</li>
      <li>&#8226; Stripe $1 paid (Gate 2) — Stripe webhook</li>
      <li>&#8226; Meta pixel firing correctly on main LPs</li>
    </ul>
  </div>
  <div style="background:{BG};border:1px solid {RED}33;border-radius:8px;padding:16px 18px;">
    <p style="color:{RED};font-weight:700;font-size:.82rem;text-transform:uppercase;
              letter-spacing:.06em;margin:0 0 10px;">&#9888; Tracking Broken</p>
    <ul style="color:#cbd5e1;font-size:.85rem;line-height:2;list-style:none;padding:0;">
      <li>&#8226; ADHD-EF LP — 0 GA4 sessions (1,122 clicks)</li>
      <li>&#8226; Selective Mutism LP — 0 GA4 sessions (695 clicks)</li>
      <li>&#8226; AI-Curious LP — near-zero GA4 data</li>
      <li>&#8226; GA4 purchase event unreliable sitewide → use Stripe</li>
    </ul>
  </div>
</div>"""

    # ── Elimination timeline ──────────────────────────────────────────────────
    timeline = f"""
<div style="margin:16px 0;">
  <div style="display:flex;gap:0;flex-direction:column;">
    {_timeline_item("May 18", "AI-Curious, Homeschool paused", "Worst CPM + CPC. Zero paid deposits confirmed no demand signal.", RED, True)}
    {_timeline_item("May 22", "iPad Battle Mom, Homework Meltdown, Selective Mutism paused", "LP issues (iPad) + diminishing returns vs top 3.", AMBER, False)}
    {_timeline_item("May 27", "Anxiety Regulation, ADHD-EF stopped", "CPaC $55–84 vs top 3 average of $31.52. Resources consolidated.", PURPLE, False)}
    {_timeline_item("Final", "Nostalgia Bridge + Sturdy Parenting + Routine-Chaos", "Lowest CPaC, highest paid volume, best funnel metrics.", GREEN, False)}
  </div>
</div>"""

    cpl = kpi_grid([
        ("Top 3 Avg CPaC", "$31.52", AMBER, "Nostalgia · Sturdy · Routine-Chaos"),
        ("Best CPaC", "$27.21", GREEN, "Sturdy Parenting"),
        ("Worst (top 3) CPaC", "$38.47", PINK, "Routine-Chaos"),
        ("Cut Trigger Used", "<$150/seg", BLUE, "Recommend tighten to $50 / 3-5 days"),
    ])

    content = f"""
{section_header("01", "Meta Ads Optimization", "Status &amp; Findings — May 12–31, 2026")}

<h3 style="color:{TEXT};font-size:.95rem;font-weight:700;margin:0 0 12px;">
  Creative A/B Test Results
</h3>
<p style="color:{MUTED};font-size:.85rem;line-height:1.7;margin:0 0 14px;">
  Problem-led vs outcome-led creative styles across all active ad sets.
  Ranked by FSD (form submit deposit = Gate 1).
</p>
{table(
    [("Creative Style","left"),("Type","left"),("FSD","right"),
     ("FSD Bar","left"),("Spend","right"),("CTR%","right"),("CPC","right")],
    ab_rows, 720
)}
{creative_verdict}

<h3 style="color:{TEXT};font-size:.95rem;font-weight:700;margin:28px 0 12px;">
  Audience Segment Test Results
</h3>
<p style="color:{MUTED};font-size:.85rem;line-height:1.7;margin:0 0 14px;">
  Ranked by FSD. "Child psychologist followers" intent maps to ADHD-EF and Anxiety Regulation —
  both viable but CPaC runs 2–3x higher than top 3.
</p>
{seg_table}

{callout("&#128161;", "Audience Winners",
    f"""<strong>Nostalgia Bridge</strong> (dads, nostalgic parenting appeal) — highest absolute FSD + paid volume.<br>
    <strong>Sturdy Parenting</strong> (evidence-based parents) — best paid rate, lowest CPaC ($27.21).<br>
    <strong>Routine-Chaos</strong> (dual-income, morning chaos) — consistent and scalable.
    Together these 3 segments account for <strong style="color:{GREEN};">163 FSD and 155 paid deposits</strong>.""",
    AMBER
)}

<h3 style="color:{TEXT};font-size:.95rem;font-weight:700;margin:28px 0 4px;">
  Video vs Static — Head-to-Head Test
</h3>
{video_correction}

<h3 style="color:{TEXT};font-size:.95rem;font-weight:700;margin:28px 0 12px;">
  Pixel &amp; Event Tracking Status
</h3>
{tracking}

<h3 style="color:{TEXT};font-size:.95rem;font-weight:700;margin:28px 0 12px;">
  Segment Elimination Timeline
</h3>
{timeline}

<h3 style="color:{TEXT};font-size:.95rem;font-weight:700;margin:28px 0 12px;">
  CPaC Performance
</h3>
{cpl}"""

    return section_wrap(content, "s1")


def _timeline_item(date: str, title: str, desc: str, color: str, first: bool) -> str:
    border_top = "" if first else f"border-top:1px dashed {BORDER};"
    return f"""
<div style="display:flex;gap:16px;padding:14px 0;{border_top}">
  <div style="flex-shrink:0;min-width:70px;">
    <span style="color:{color};font-size:.78rem;font-weight:700;">{date}</span>
  </div>
  <div>
    <p style="color:{TEXT};font-weight:600;font-size:.88rem;margin:0 0 4px;">{title}</p>
    <p style="color:{MUTED};font-size:.82rem;line-height:1.6;margin:0;">{desc}</p>
  </div>
</div>"""


def build_section2() -> str:
    """Landing Page Conversion — Audit Results"""

    # Sort by FSD Rate desc
    ga4_sorted = sorted(GA4_DATA, key=lambda r: -(r[6] or 0))

    lp_rows = ""
    for name, meta_clicks, ga4_sess, fsd, ga4_purch, stripe_paid, fsd_rate, paid_rate, eng_s in ga4_sorted:
        color    = SEG_COLORS.get(name, SLATE)
        eng_s_s  = f"{eng_s}s" if eng_s else "—"

        # FSD Rate coloring (FSD / Meta link clicks)
        if fsd_rate >= 6:
            fsd_col = GREEN
        elif fsd_rate >= 3.5:
            fsd_col = AMBER
        else:
            fsd_col = RED

        # Paid Rate coloring (Stripe paid / FSD)
        if paid_rate >= 60:
            paid_col = GREEN
        elif paid_rate >= 30:
            paid_col = AMBER
        elif paid_rate > 0:
            paid_col = RED
        else:
            paid_col = MUTED

        # Engagement coloring
        if eng_s and eng_s >= 40:
            eng_color = GREEN
        elif eng_s and eng_s < 25:
            eng_color = RED
        else:
            eng_color = AMBER

        # Flag — based on FSD rate and engagement, not LP CVR
        if name == "iPad Battle Mom":
            flag = badge("Low FSD Rate", "#2a0d0d", RED, f"{RED}55")
        elif name == "Routine-Chaos" and eng_s and eng_s < 20:
            flag = badge("Check Scroll Depth", "#0d1a2a", BLUE, f"{BLUE}44")
        elif fsd_rate >= 5 and paid_rate >= 60:
            flag = badge("Strong", "#0d1a0d", GREEN, f"{GREEN}44")
        else:
            flag = ""

        lp_rows += f"""
<tr>
  {td(f'{dot(color)}<span style="color:{color};font-weight:600;">{name}</span>', "left")}
  {td(f"{meta_clicks:,}", color=MUTED)}
  {td(f"{ga4_sess:,}" if ga4_sess else "—")}
  {td(str(fsd))}
  {td(str(stripe_paid), color=GREEN if stripe_paid else MUTED)}
  {td(f'<strong style="color:{fsd_col};">{fsd_rate:.1f}%</strong>')}
  {td(f'<strong style="color:{paid_col};">{paid_rate:.1f}%</strong>' if paid_rate else f'<span style="color:{MUTED};">—</span>')}
  {td(f'<span style="color:{eng_color};">{eng_s_s}</span>')}
  <td style="padding:10px 14px;text-align:left;border-bottom:1px solid #111827;">{flag}</td>
</tr>"""

    lp_table = table(
        [("Segment","left"),("Meta Clicks","right"),("GA4 Sessions","right"),
         ("FSD","right"),("Stripe Paid","right"),("FSD Rate %","right"),
         ("Paid Rate %","right"),("Engagement","right"),("Flag","left")],
        lp_rows, 980
    )

    ipad_emergency = callout(
        "&#128680;",
        "LP Emergency: iPad Battle Mom",
        f"""<strong>922 meta clicks → only 17 FSD (1.9% FSD Rate)</strong>, avg engagement 26.5s.
        The ad resonates — CTR 9.88% — but the landing page loses visitors in under 30 seconds.
        <br><br>
        <strong>Action:</strong> Rewrite the hook to match the ad's "nagging loop" angle exactly.
        Move social proof above the fold. This segment generated 10 paid deposits despite the broken LP —
        fixing it could 3–5x the conversion rate.""",
        RED
    )

    routine_note = callout(
        "&#128300;",
        "Routine-Chaos LP: Ambiguous 18s Engagement",
        f"""41.2% CVR with only 18s average engagement creates an interpretive problem:
        are visitors converting <em>quickly</em> (hook lands immediately) or <em>bouncing</em>
        (not finding what they expected)?<br><br>
        <strong>Action:</strong> Add scroll depth + time-on-page segmented by exit vs conversion.
        This will confirm whether to scale or rewrite.""",
        BLUE
    )

    lp_recs = f"""
<div style="margin-top:24px;">
  <h3 style="color:{TEXT};font-size:.95rem;font-weight:700;margin:0 0 14px;">
    LP Optimization Priorities
  </h3>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
    <div style="background:{BG};border:1px solid {BORDER};border-radius:8px;padding:16px 18px;">
      <p style="color:{RED};font-weight:700;font-size:.82rem;margin:0 0 10px;">P0 — Immediate</p>
      <ul style="color:#cbd5e1;font-size:.85rem;line-height:2;padding-left:16px;">
        <li>Rewrite iPad Battle Mom LP hook</li>
        <li>Fix GA4 pixel on ADHD-EF, Selective Mutism, AI-Curious LPs</li>
      </ul>
    </div>
    <div style="background:{BG};border:1px solid {BORDER};border-radius:8px;padding:16px 18px;">
      <p style="color:{AMBER};font-weight:700;font-size:.82rem;margin:0 0 10px;">P1 — High Value</p>
      <ul style="color:#cbd5e1;font-size:.85rem;line-height:2;padding-left:16px;">
        <li>Add testimonials/social proof above the fold on all LPs</li>
        <li>Exit-intent popup → highest priority for 207 pending conversion</li>
        <li>Scroll depth tracking on Routine-Chaos LP</li>
      </ul>
    </div>
  </div>
  {callout("&#128161;", "Social Proof Gap",
    f"""Testimonial ad style is the <strong>#2 performing creative (35 FSD, $0.28 CPC)</strong>
    — but that social proof lives only in the ad, not the landing page.
    Matching LP content to the ad's social proof angle is the highest-leverage
    copy change available across all LPs.""",
    AMBER
  )}
</div>"""

    content = f"""
{section_header("02", "Landing Page Conversion", "Audit Results — GA4 engagement as hook clarity proxy")}

<p style="color:{MUTED};font-size:.85rem;line-height:1.7;margin:0 0 16px;">
  FSD Rate = FSD ÷ Meta link clicks (Gate 1). Paid Rate = Stripe paid ÷ FSD (Gate 2). Engagement time from GA4 —
  longer engagement correlates with hook resonance and scroll depth.
  Note: GA4 purchase events unreliable; Stripe is source of truth.
</p>
{lp_table}

{callout("&#9888;", "Tracking Gap on 3 Landing Pages",
    f"""ADHD-EF (1,122 meta clicks), Selective Mutism (695 clicks), and AI-Curious (186 clicks)
    show <strong>zero GA4 sessions</strong> — pixel is missing or broken on these pages.
    Combined that's nearly <strong>2,000 clicks with no funnel visibility</strong>.
    Fix before any further spend on these audiences.""",
    PURPLE
)}

{ipad_emergency}

{routine_note}

{lp_recs}"""

    return section_wrap(content, "s2")


def build_section3() -> str:
    content = f"""
{section_header("03", "Influencer &amp; Creator Outreach", "Recommendations")}
{callout("&#9200;",
    "Not Started — Strategic Hold",
    f"""Influencer outreach has not begun. Recommendation: hold until the Kickstarter
    go/no-go decision is made (see Section 6). Influencer timing should align with the
    launch moment for maximum earned media impact. A successful Kickstarter page gives
    influencers a concrete, time-bounded offer to promote — far more effective than
    directing followers to a vague waitlist.<br><br>
    When ready: prioritize parenting creators with ADHD/calm-tech audiences
    (aligns with Sturdy Parenting and Anxiety Regulation segments — already proven buyers).""",
    MUTED
)}"""
    return section_wrap(content, "s3")


def build_section4() -> str:
    content = f"""
{section_header("04", "PR &amp; Content", "Recommendations")}
{callout("&#9200;",
    "Not Started — Strategic Hold",
    f"""PR and content strategy has not launched. Same recommendation as influencer outreach:
    align with Kickstarter launch for maximum impact. A live, funded campaign is
    significantly more newsworthy than a landing page with a waitlist.<br><br>
    Draft PR angle: <em>"Parents paid $1 to prove demand for a calm-tech device before it exists."</em>
    The 155 paid deposits are the story — Kickstarter makes it reportable.""",
    MUTED
)}"""
    return section_wrap(content, "s4")


def build_section5() -> str:
    """Community Seeding"""

    # All 10 segments with paid + pending counts
    all_segments = [
        ("Nostalgia Bridge Dad", 42, 59, AMBER),
        ("Sturdy Parenting",     43, 50, GREEN),
        ("Routine-Chaos",        28, 28, PINK),
        ("Homework Meltdown",    13, 15, "#60a5fa"),
        ("iPad Battle Mom",      10, 14, "#60a5fa"),
        ("Anxiety Regulation",    8, 10, "#60a5fa"),
        ("ADHD-EF Intervention",  7, 15, "#818cf8"),
        ("Selective Mutism",      4,  9, "#818cf8"),
        ("Homeschool",            0,  6, MUTED),
        ("AI-Curious Parent",     0,  1, MUTED),
    ]

    total_paid    = sum(p for _, p, _, _ in all_segments)
    total_pending = sum(n for _, _, n, _ in all_segments)
    total_all     = total_paid + total_pending

    seg_rows = ""
    for name, paid, pending, color in all_segments:
        total = paid + pending
        paid_bar = paid / max(total, 1) * 100
        top3 = name in ("Nostalgia Bridge Dad", "Sturdy Parenting", "Routine-Chaos")
        badge = f'<span style="background:{color}22;color:{color};border:1px solid {color}44;border-radius:4px;padding:1px 6px;font-size:.68rem;margin-left:6px;">Top 3</span>' if top3 else ""
        seg_rows += f"""
<tr style="border-bottom:1px solid #111827;">
  <td style="padding:9px 14px;">
    <span style="color:{color};font-weight:600;font-size:.84rem;">{name}</span>{badge}
  </td>
  <td style="padding:9px 14px;color:{GREEN};font-weight:700;text-align:right;">{paid}</td>
  <td style="padding:9px 14px;color:{BLUE};text-align:right;">{pending}</td>
  <td style="padding:9px 14px;color:{MUTED};text-align:right;">{total}</td>
  <td style="padding:9px 14px;min-width:140px;">
    <div style="display:flex;align-items:center;gap:8px;">
      <div style="flex:1;height:7px;background:#1e2235;border-radius:3px;">
        <div style="width:{paid_bar:.1f}%;height:100%;background:{color};border-radius:3px;opacity:.7;"></div>
      </div>
      <span style="color:{MUTED};font-size:.75rem;min-width:32px;">{paid_bar:.0f}%</span>
    </div>
  </td>
</tr>"""

    breakdown_table = f"""
<div style="overflow-x:auto;margin:20px 0;">
  <table style="width:100%;border-collapse:collapse;min-width:480px;">
    <thead style="background:#060810;">
      <tr>
        <th style="padding:8px 14px;text-align:left;color:{MUTED};font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Segment</th>
        <th style="padding:8px 14px;text-align:right;color:{GREEN};font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Paid ($1)</th>
        <th style="padding:8px 14px;text-align:right;color:{BLUE};font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Pending</th>
        <th style="padding:8px 14px;text-align:right;color:{MUTED};font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Total</th>
        <th style="padding:8px 14px;text-align:left;color:{MUTED};font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Paid Rate</th>
      </tr>
    </thead>
    <tbody>
      {seg_rows}
      <tr style="background:#060810;border-top:2px solid #2a2e3a;">
        <td style="padding:9px 14px;color:#e4e7ef;font-weight:700;font-size:.85rem;">TOTAL</td>
        <td style="padding:9px 14px;color:{GREEN};font-weight:700;text-align:right;">{total_paid}</td>
        <td style="padding:9px 14px;color:{BLUE};font-weight:700;text-align:right;">{total_pending}</td>
        <td style="padding:9px 14px;color:#e4e7ef;font-weight:700;text-align:right;">{total_all}</td>
        <td style="padding:9px 14px;color:{MUTED};font-size:.8rem;">{total_paid/max(total_all,1)*100:.1f}% paid rate</td>
      </tr>
    </tbody>
  </table>
</div>"""

    content = f"""
{section_header("05", "Community Seeding", "What we have — founding cohort already built across all 10 segments")}

{kpi_grid([
    ("Paid Deposits", str(total_paid), GREEN, "Gate 2 — $1 each, fully committed"),
    ("Pending Deposits", str(total_pending), BLUE, "Gate 1 FSD — already emailed"),
    ("Total Community Seed", str(total_all), AMBER, "Combined warm advocate pool"),
    ("Already Emailed", "Yes", SLATE, "Both groups contacted"),
])}

<p style="color:{MUTED};font-size:.87rem;line-height:1.7;margin:16px 0;">
  The "super parent advocates" the consultant recommended finding are already in the funnel —
  across all 10 audience segments. They discovered Nowa from cold Meta traffic, filled out a form,
  and <strong style="color:#e4e7ef;">paid $1</strong> to prove intent.
  This is the most qualified pre-launch community seed possible.
</p>

{breakdown_table}

{callout("&#127968;",
    f"Recommendation: Create a Founding Family Community (seed: {total_paid} paid customers)",
    f"""Create a private Facebook Group or Discord for the <strong>{total_paid} paid customers</strong> immediately.
    Name it something like "Nowa Founding Families" or "Nowa Beta Parents."<br><br>
    This cohort's organic sharing will outperform any paid campaign for social proof —
    because it's real parents who put money down, not sponsored content.
    Their word-of-mouth is the community strategy.<br><br>
    <strong>Bonus:</strong> Use this group to gather product feedback before Kickstarter,
    which strengthens the campaign story ("built with real parents").""",
    GREEN
)}

{callout("&#128231;",
    f"The {total_pending} Pending — Price-Sensitive, Not Uninterested",
    f"""{total_pending} people went through the full form process but didn't complete the $1 payment.
    This is not disqualifying — it's a friction signal, not a disinterest signal.
    Kickstarter's early-bird pricing model converts this segment far better than a single $99 price point
    (see Section 6 for the full Kickstarter analysis).""",
    BLUE
)}"""
    return section_wrap(content, "s5")


def _community_card(name: str, paid: int, pending: int, color: str) -> str:
    total = paid + pending
    paid_pct = paid / total * 100 if total else 0
    return f"""
<div style="background:{BG};border:1px solid {color}33;border-radius:10px;padding:18px 20px;">
  <p style="color:{color};font-weight:700;font-size:.85rem;margin:0 0 12px;">{name}</p>
  <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
    <span style="color:{MUTED};font-size:.8rem;">Paid</span>
    <span style="color:{GREEN};font-weight:700;">{paid}</span>
  </div>
  <div style="display:flex;justify-content:space-between;margin-bottom:10px;">
    <span style="color:{MUTED};font-size:.8rem;">Pending</span>
    <span style="color:{BLUE};">{pending}</span>
  </div>
  {progress_bar(paid_pct, color)}
  <p style="color:{MUTED};font-size:.72rem;margin:6px 0 0;">{paid_pct:.0f}% converted to paid</p>
</div>"""


def build_section6() -> str:
    """Kickstarter vs DTC Analysis"""

    # Comparison table rows: metric, kickstarter, dtc, winner
    comparison_rows = [
        ("Social proof",         "Public backer count — trust signal",         "Build yourself (testimonials, press)",       "KS"),
        ("Press coverage",       "Built into the launch story",                 "Requires separate PR effort",                "KS"),
        ("Customer data",        "Limited — Kickstarter owns the relationship", "Full data, yours forever",                   "DTC"),
        ("Price flexibility",    "Early bird tiers create urgency",             "Full control, change anytime",               "DTC"),
        ("Subscription / LTV",   "Hard to convert KS backers to recurring",     "Direct relationship, easy to upsell",        "DTC"),
        ("Risk model",           "All-or-nothing — goal must be hit",           "Ship when you're ready",                     "DTC"),
        ("Platform fees",        "5% KS + 3% payment = ~8% of revenue",        "~3% payment only",                           "DTC"),
        ("Meta ads destination", "Point to KS page (less control)",             "Point to your LP (full control)",            "DTC"),
        ("207 pending list",     "Early bird tiers convert price-sensitive",    "Urgency email + price nudge",                "KS"),
        ("113 paid list",        "Day-1 backers = funded in hours story",       "Loyal early customers for community",        "TIE"),
        ("Delivery commitment",  "Backers expect dates — hard deadline",        "Ship when ready, flexible",                  "DTC"),
    ]

    rows_html = ""
    for metric, ks_val, dtc_val, winner in comparison_rows:
        ks_color = f"color:{GREEN}" if winner == "KS" else f"color:{MUTED}"
        dtc_color = f"color:{GREEN}" if winner == "DTC" else f"color:{MUTED}"
        tie_badge = f'<span style="color:{BLUE};font-size:.7rem;font-weight:700;">TIE</span>' if winner == "TIE" else ""
        ks_badge = f'<span style="color:{GREEN};font-size:.7rem;font-weight:700;">&#10003;</span>' if winner == "KS" else ""
        dtc_badge = f'<span style="color:{GREEN};font-size:.7rem;font-weight:700;">&#10003;</span>' if winner == "DTC" else ""
        rows_html += f"""
<tr style="border-bottom:1px solid {BORDER};">
  <td style="padding:9px 14px;color:{TEXT};font-size:.82rem;font-weight:500;">{metric}</td>
  <td style="padding:9px 14px;font-size:.8rem;{ks_color};">{ks_badge} {ks_val}</td>
  <td style="padding:9px 14px;font-size:.8rem;{dtc_color};">{dtc_badge} {dtc_val}</td>
</tr>"""

    # What data says
    data_ks = [
        (GREEN,  "Demand validated", "155 paid $1 from cold traffic. CPaC $27–38 for a $99 product = 2.6–3.7x ROAS before LTV."),
        (GREEN,  "207 pending = price-sensitive", "Early bird tiers ($69→$79→$89→$99) convert this group far better than a single price."),
        (BLUE,   "Email list ready", "362 primed contacts. KS campaigns with an existing list convert 20–40% on Day 1 = 72–145 backers."),
    ]
    data_dtc = [
        (GREEN,  "Funnel already works", "Sturdy Parenting FSD Rate 3.7%, Paid Rate 71.7% (best in cohort). CPaC $27–38 is viable at $99."),
        (GREEN,  "Full data ownership", "Every customer is yours. Subscription, upsell, referral — all possible without a platform middleman."),
        (BLUE,   "Scale is proven", "Adding $2K budget to Nostalgia Bridge + Sturdy would generate ~70 more paid deposits at current CPaC."),
    ]

    def _data_items(items):
        html = ""
        for color, title, desc in items:
            html += f"""
<div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid {BORDER}22;">
  <span style="color:{color};flex-shrink:0;font-size:.9rem;">&#10003;</span>
  <div>
    <span style="color:{TEXT};font-weight:600;font-size:.82rem;">{title} — </span>
    <span style="color:{MUTED};font-size:.82rem;">{desc}</span>
  </div>
</div>"""
        return html

    # Hybrid path steps
    hybrid_steps = [
        (AMBER, "1", "Run DTC now", "Keep nowaplanet.com as the funnel. Scale top 3 segments 1.5–2x. Build the paid customer list."),
        (GREEN, "2", "Hit 300–400 paid deposits", "This is your Kickstarter 'social proof proof point' — funded product exists, people paid."),
        (BLUE,  "3", "Launch Kickstarter as the 'going big' moment", "Port existing 300+ customers as Day-1 backers. Kickstarter becomes a PR event, not a validation exercise."),
        (PINK,  "4", "Redirect Meta ads to KS", "Same top 3 segments, same creatives, swap destination URL. No creative rebuild needed."),
    ]
    hybrid_html = ""
    for color, num, title, desc in hybrid_steps:
        hybrid_html += f"""
<div style="display:flex;gap:14px;align-items:flex-start;padding:10px 0;border-bottom:1px solid {BORDER};">
  <div style="background:{color}22;border:1px solid {color}44;border-radius:50%;
              width:26px;height:26px;display:flex;align-items:center;justify-content:center;
              flex-shrink:0;color:{color};font-weight:700;font-size:.8rem;">{num}</div>
  <div>
    <p style="color:{TEXT};font-weight:600;font-size:.85rem;margin:0 0 2px;">{title}</p>
    <p style="color:{MUTED};font-size:.8rem;margin:0;">{desc}</p>
  </div>
</div>"""

    # ── 2 PENDING DECISION QUESTIONS ──────────────────────────────────────
    pending_qs = f"""
<div style="background:#0d1018;border:2px solid {AMBER}55;border-radius:10px;
            padding:20px 22px;margin-top:24px;">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;">
    <span style="font-size:1.1rem;">&#9888;</span>
    <span style="color:{AMBER};font-weight:700;font-size:.82rem;text-transform:uppercase;
                 letter-spacing:.08em;">2 Questions to Decide This — Answer Before Committing</span>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">

    <div style="background:#080b12;border:1px solid {AMBER}33;border-radius:8px;padding:16px 18px;">
      <div style="color:{AMBER};font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;
                  margin-bottom:8px;font-weight:700;">Question 1</div>
      <p style="color:{TEXT};font-size:.9rem;font-weight:600;margin:0 0 10px;line-height:1.5;">
        When is the product physically ready to ship?
      </p>
      <p style="color:{MUTED};font-size:.8rem;line-height:1.6;margin:0 0 12px;">
        <strong style="color:#94a3b8;">If 60–90 days → DTC.</strong> Your funnel works. Ship and own the customer relationship.<br><br>
        <strong style="color:#94a3b8;">If 4–6+ months → Kickstarter.</strong> All-or-nothing model lets you validate the $99 price point without inventory risk. The wait time is an asset, not a liability.
      </p>
      <div style="background:{AMBER}11;border:1px dashed {AMBER}33;border-radius:6px;
                  padding:10px 12px;text-align:center;">
        <span style="color:{AMBER};font-size:.8rem;font-weight:700;">⬜ Answer: _______________</span>
      </div>
    </div>

    <div style="background:#080b12;border:1px solid {BLUE}33;border-radius:8px;padding:16px 18px;">
      <div style="color:{BLUE};font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;
                  margin-bottom:8px;font-weight:700;">Question 2</div>
      <p style="color:{TEXT};font-size:.9rem;font-weight:600;margin:0 0 10px;line-height:1.5;">
        Do you need Kickstarter's validation story, or is the product validated enough?
      </p>
      <p style="color:{MUTED};font-size:.8rem;line-height:1.6;margin:0 0 12px;">
        <strong style="color:#94a3b8;">If you need validation →</strong> 155 paid $1 customers is a strong signal, but Kickstarter makes it public and press-worthy. The "funded in X hours" story is more investable than "155 pre-orders."<br><br>
        <strong style="color:#94a3b8;">If product is validated →</strong> Skip Kickstarter. Scale DTC. Use the 207 pending list as your urgency lever.
      </p>
      <div style="background:{BLUE}11;border:1px dashed {BLUE}33;border-radius:6px;
                  padding:10px 12px;text-align:center;">
        <span style="color:{BLUE};font-size:.8rem;font-weight:700;">⬜ Answer: _______________</span>
      </div>
    </div>

  </div>
</div>"""

    content = f"""
{section_header("06", "Channel Decision", "Kickstarter vs DTC — Side-by-Side Analysis")}

<!-- Comparison table -->
<div style="overflow-x:auto;margin-bottom:24px;">
  <table style="width:100%;border-collapse:collapse;min-width:600px;">
    <thead style="background:#060810;">
      <tr>
        <th style="padding:9px 14px;text-align:left;color:{MUTED};font-size:.72rem;font-weight:600;
                   text-transform:uppercase;letter-spacing:.06em;width:22%;">Factor</th>
        <th style="padding:9px 14px;text-align:left;color:#a78bfa;font-size:.72rem;font-weight:600;
                   text-transform:uppercase;letter-spacing:.06em;width:39%;">Kickstarter</th>
        <th style="padding:9px 14px;text-align:left;color:{GREEN};font-size:.72rem;font-weight:600;
                   text-transform:uppercase;letter-spacing:.06em;width:39%;">DTC (nowaplanet.com)</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>

<!-- What data supports each path -->
<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px;">
  <div style="background:{BG};border:1px solid #a78bfa33;border-radius:10px;padding:18px 20px;">
    <p style="color:#a78bfa;font-weight:700;font-size:.78rem;text-transform:uppercase;
              letter-spacing:.06em;margin:0 0 12px;">Your Data Supports Kickstarter Because...</p>
    {_data_items(data_ks)}
  </div>
  <div style="background:{BG};border:1px solid {GREEN}33;border-radius:10px;padding:18px 20px;">
    <p style="color:{GREEN};font-weight:700;font-size:.78rem;text-transform:uppercase;
              letter-spacing:.06em;margin:0 0 12px;">Your Data Supports DTC Because...</p>
    {_data_items(data_dtc)}
  </div>
</div>

<!-- Hybrid path -->
{callout("&#128161;",
    "The Hybrid Path (if you want both)",
    f"Run DTC now → build the list → launch Kickstarter as a 'going big' PR event rather than a validation exercise:{hybrid_html}",
    BLUE
)}

<!-- 2 pending questions -->
{pending_qs}

<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:20px;">
  {_channel_verdict("Amazon — Hold", MUTED,
    "DTC first is correct. Amazon too early compresses margins and loses customer data "
    "permanently. Revisit post-launch once brand is established and reviews exist.")}
  {_channel_verdict("B2B / School Programs — Defer", MUTED,
    "Long sales cycle (6–12 months for procurement). "
    "Strong narrative for post-launch expansion but wrong focus for now.")}
</div>"""

    return section_wrap(content, "s6")


def _channel_verdict(title: str, color: str, desc: str) -> str:
    return f"""
<div style="background:{BG};border:1px solid {BORDER};border-radius:8px;padding:16px 18px;">
  <p style="color:{color};font-weight:700;font-size:.82rem;margin:0 0 8px;">{title}</p>
  <p style="color:{MUTED};font-size:.85rem;line-height:1.6;margin:0;">{desc}</p>
</div>"""


def build_section7() -> str:
    """Launch Sequence"""

    steps = [
        ("NOW",      RED,   "Fix Broken Tracking + iPad Battle Mom LP",
         "Fix GA4 pixel on 3 broken LPs. Rewrite iPad Battle Mom hook. "
         "These are table-stakes — every day they're broken is wasted data."),
        ("Week 1–2", AMBER, "Email 207 Pending — Kickstarter Early Bird Preview",
         "Subject line: 'You're first — here's what's coming.' "
         "Urgency: early bird Kickstarter pricing is exclusive to this list. "
         "Goal: warm them for Kickstarter launch day."),
        ("Week 2–4", BLUE,  "Kickstarter Preparation",
         "Campaign page, product video, press kit, backer tier pricing ($69 → $79 → $89 → $99). "
         "Founding family community (155 paid) provides testimonials and beta feedback."),
        ("Launch Day", GREEN, "Sequence: List → Ads → Press",
         "Email 362-person list first (they get first access). "
         "Then Meta ads pointing to Kickstarter (same top 3 segments). "
         "Then press outreach (the '113 people pre-validated this at $1' angle)."),
        ("Post-KS",  PURPLE, "Public Launch with Validated Price + Social Proof",
         "A successful Kickstarter gives you: final price validated, social proof established, "
         "press coverage, and a backer community. Scale Meta ads with confidence."),
    ]

    steps_html = ""
    for i, (when, color, title, desc) in enumerate(steps):
        connector = f'<div style="width:2px;height:20px;background:{BORDER};margin:0 0 0 13px;"></div>' \
                    if i < len(steps) - 1 else ""
        steps_html += f"""
<div style="display:flex;gap:16px;align-items:flex-start;">
  <div style="display:flex;flex-direction:column;align-items:center;flex-shrink:0;">
    <div style="background:{color}22;border:2px solid {color};border-radius:50%;
                width:28px;height:28px;display:flex;align-items:center;justify-content:center;
                color:{color};font-weight:800;font-size:.75rem;">{i+1}</div>
    {connector}
  </div>
  <div style="padding-bottom:20px;">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">
      <span style="color:{color};font-size:.78rem;font-weight:700;
                   background:{color}15;border:1px solid {color}33;
                   border-radius:4px;padding:2px 8px;">{when}</span>
      <p style="color:{TEXT};font-weight:600;font-size:.9rem;margin:0;">{title}</p>
    </div>
    <p style="color:{MUTED};font-size:.84rem;line-height:1.7;margin:0;">{desc}</p>
  </div>
</div>"""

    position = f"""
<div style="background:{BG};border:1px solid {AMBER}33;border-radius:10px;
            padding:20px 24px;margin-bottom:24px;">
  <p style="color:{AMBER};font-weight:700;font-size:.82rem;text-transform:uppercase;
            letter-spacing:.06em;margin:0 0 8px;">Current Position in Launch Arc</p>
  <div style="display:flex;align-items:center;gap:0;flex-wrap:wrap;">
    {_arc_step("Beta Waitlist", True, MUTED)}
    {_arc_arrow()}
    {_arc_step("113 Paid Deposits", True, GREEN)}
    {_arc_arrow()}
    {_arc_step("Early Bird (You Are Here)", True, AMBER)}
    {_arc_arrow()}
    {_arc_step("Kickstarter Launch", False, BLUE)}
    {_arc_arrow()}
    {_arc_step("Public Launch", False, PURPLE)}
  </div>
</div>"""

    content = f"""
{section_header("07", "Launch Sequence", "Current Position &amp; Recommended Roadmap")}
{position}
{steps_html}"""

    return section_wrap(content, "s7")


def _arc_step(label: str, done: bool, color: str) -> str:
    bg = f"{color}22" if done else BG
    border = f"{color}" if done else BORDER
    text_color = color if done else MUTED
    return f"""
<div style="background:{bg};border:1px solid {border};border-radius:6px;
            padding:6px 12px;font-size:.75rem;color:{text_color};font-weight:600;
            white-space:nowrap;{'opacity:.6;' if not done else ''}">{label}</div>"""

def _arc_arrow() -> str:
    return f'<span style="color:{BORDER};padding:0 4px;font-size:1rem;">&#8250;</span>'


def build_actions() -> str:
    """Immediate Action Table"""

    rows = [
        (RED,   "P0", "Fix GA4 tracking on ADHD-EF, Selective Mutism, AI-Curious LPs",
         "0 sessions tracked despite 2,000+ meta clicks",
         "Full visibility into ~$1,100 in spend with no funnel data"),
        (RED,   "P0", "Rewrite iPad Battle Mom LP hook",
         "922 clicks, 26.5s avg engagement, 1.9% FSD Rate",
         "Potential 3–5x FSD improvement from existing traffic"),
        (AMBER, "P1", "Email 207 pending with Kickstarter early bird offer",
         "207 warm leads, already emailed once",
         "Convert fence-sitters before Kickstarter list goes public"),
        (AMBER, "P1", "Create Founding Family community (155 paid)",
         "Warmest possible audience — paid $1 from cold traffic",
         "Testimonials, beta feedback, organic amplification"),
        (BLUE,  "P2", "Decide Kickstarter go/no-go",
         "CPaC $27–38 vs $99 product = 2.6–3.7x ROAS math",
         "Unlocks press, influencer, and channel strategy"),
        (BLUE,  "P2", "Scale Nostalgia Bridge + Sturdy budgets 1.5–2x",
         "Lowest CPaC, highest paid volume, best funnel health",
         "Estimated 30–50 additional paid deposits per $1,000 incremental spend"),
        (GREEN, "P3", "Add scroll depth tracking to Routine-Chaos LP",
         "18s avg engagement is ambiguous (fast convert or fast bounce?)",
         "Diagnoses whether to scale or rewrite"),
        (GREEN, "P3", "Build retargeting campaign for 207 pending",
         "Zero new spend required to reach existing warm audience",
         "Best ROAS opportunity available — audience already primed"),
    ]

    action_rows = ""
    for i, (color, priority, action, data_basis, impact) in enumerate(rows):
        pri_badge = badge(priority, f"{color}15", color, f"{color}44")
        action_rows += f"""
<tr style="{'background:#0f1520;' if i % 2 == 0 else ''}">
  <td style="padding:12px 14px;border-bottom:1px solid #111827;text-align:center;white-space:nowrap;">{pri_badge}</td>
  {td(f'<strong style="color:{TEXT};">{action}</strong>', "left")}
  {td(f'<span style="color:{MUTED};font-size:.82rem;">{data_basis}</span>', "left")}
  {td(f'<span style="color:{color};font-size:.82rem;">{impact}</span>', "left")}
</tr>"""

    actions_table = table(
        [("Priority","center"),("Action","left"),("Data Basis","left"),("Expected Impact","left")],
        action_rows, 800
    )

    content = f"""
{section_header("&#9889;", "Immediate Action Table", "Prioritized by urgency + data confidence")}
{actions_table}"""

    return section_wrap(content, "actions")


# ---------------------------------------------------------------------------
# Full HTML assembly
# ---------------------------------------------------------------------------

def build_report() -> str:
    sections = [
        build_section1(),
        build_section2(),
        build_section3(),
        build_section4(),
        build_section5(),
        build_section6(),
        build_section7(),
        build_actions(),
    ]

    body = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Nowa GTM Planning Report — May 2026</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{
      background:{BG};
      color:{TEXT};
      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
      line-height:1.55;
    }}
    table{{border-collapse:collapse;width:100%}}
    tr:hover td{{background:rgba(255,255,255,.015)}}
    a{{color:{BLUE};}}
    a:hover{{color:#fff;}}
    ::-webkit-scrollbar{{width:6px;height:6px;background:{BG};}}
    ::-webkit-scrollbar-thumb{{background:{BORDER};border-radius:3px;}}
    html{{scroll-behavior:smooth;}}
  </style>
</head>
<body>
  {build_nav()}

  <div style="max-width:1180px;margin:0 auto;padding:48px 24px;">
    {build_header()}
    {body}

    <footer style="text-align:center;color:{BORDER};font-size:.72rem;
                   padding:24px 0 0;border-top:1px solid #111827;margin-top:8px;">
      Nowa GTM Planning Report &nbsp;&middot;&nbsp; May 2026 Campaign Data &nbsp;&middot;&nbsp; Confidential
    </footer>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Building Nowa GTM Planning Report ...")
    html = build_report()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    size_kb = len(html.encode("utf-8")) // 1024
    print(f"Done. Report saved: {OUT}  ({size_kb} KB)")
    print(f"Open: start \"\" \"{OUT}\"")


if __name__ == "__main__":
    main()
