"""Orders page — every paid order, and where it actually came from.

Answers "which ad / page produced this sale" one order at a time, rather than in
aggregate: with order counts in the tens, the per-order journey is more useful
than a conversion rate, and it is the only view that shows a segment LP doing the
introducing while the homepage takes the last-touch credit.

Built on shopify_order_journey (migration 019), which stores Shopify's own
customerJourneySummary — first visit and last visit, each with their own landing
page and UTM parameters. The REST orders endpoint cannot express that.

Standalone: no aiogram / src.ai / asyncio imports (D-19 standalone-page rule).
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Orders",
    layout="wide",
    initial_sidebar_state="expanded",
)

from src.dashboard import db                            # noqa: E402
from src.dashboard.components import source_line        # noqa: E402
from src.dashboard.settings import DashboardSettings    # noqa: E402

settings = DashboardSettings()

# ---------------------------------------------------------------------------
# Auth gate — same shared-password pattern as every other page (D-21)
# ---------------------------------------------------------------------------
if settings.dashboard_password:
    if not st.session_state.get("authenticated"):
        pwd = st.text_input("Password", type="password")
        if st.button("Login"):
            if pwd == settings.dashboard_password:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password")
        st.stop()


@st.cache_data(ttl=300, show_spinner=False)
def _cached_journeys(
    db_path_str: str, valid_from: str, min_order_number: int
) -> list[dict[str, Any]]:
    from pathlib import Path
    rows = db.get_order_journeys(
        Path(db_path_str), min_order_number=min_order_number
    )
    # The date cutoff is applied here rather than in SQL so the unfiltered count
    # stays available for the "excluded" note below — a silently shorter list
    # would look like data loss.
    if valid_from:
        rows = [r for r in rows if r["order_date"] >= valid_from]
    return rows


@st.cache_data(ttl=300, show_spinner=False)
def _cached_all_journeys(db_path_str: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_order_journeys(Path(db_path_str))


db_path_str = str(settings.db_path)

st.title("Orders — where every one came from")
source_line(
    "Shop", "M",
    note="Journey + status = Shopify customerJourneySummary · ad name resolved from Meta",
)

_all_rows = _cached_all_journeys(db_path_str)
_rows = _cached_journeys(
    db_path_str, settings.orders_valid_from, settings.orders_min_order_number
)

if not _all_rows:
    st.info(
        "No order journeys ingested yet. This page populates once the Shopify "
        "order-journey ingest has run (`shopify_order_journey`, migration 019) — "
        "it needs SHOPIFY_STORE_DOMAIN / SHOPIFY_ADMIN_TOKEN configured."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Scope note — this page shows history, not a date window
# ---------------------------------------------------------------------------
_excluded = len(_all_rows) - len(_rows)
_cuts: list[str] = []
if settings.orders_min_order_number:
    _cuts.append(f"orders below **#{settings.orders_min_order_number}**")
if settings.orders_valid_from:
    _cuts.append(f"orders before **{settings.orders_valid_from}**")

if _cuts:
    st.caption(
        f"Showing {len(_rows)} orders · {_excluded} excluded ("
        + " and ".join(_cuts)
        + "). Deliberately not filtered by the date range used on other pages: "
        "with order counts this low, the whole history is the story."
    )
else:
    st.warning(
        f"**No order cutoff is configured, so all {len(_all_rows)} orders are shown "
        "— including pre-launch test orders.** Set `ORDERS_MIN_ORDER_NUMBER` to the "
        "first real order number to drop them here, and `ORDERS_VALID_FROM` to that "
        "order's date so the date-keyed tables on the other pages cut at the same "
        "point (shopify_orders stores Shopify's internal id, not the #1020-style "
        "name, so it can only be cut by date)."
    )

_paid = [r for r in _rows if r["financial_status"] == "paid"]
_pending = [r for r in _rows if r["financial_status"] == "pending"]
_refunded = [r for r in _rows if r["financial_status"] == "refunded"]
_traced_ad = [r for r in _rows if r["traced"] == "ad"]
_traced_adset = [r for r in _rows if r["traced"] == "adset"]
_traced_creative = [r for r in _rows if r["traced"] == "creative"]
_multi_touch = [r for r in _rows if (r["moments_count"] or 0) > 1]
_no_source = [
    r for r in _rows
    if not r["first_utm_content"] and not r["first_utm_source"] and not r["first_source"]
]

# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------
k1, k2, k3, k4 = st.columns(4)

k1.metric("Orders", f"{len(_rows):,}")
k1.caption(
    f"{len(_paid)} paid"
    + (f" · {len(_pending)} pending" if _pending else "")
    + (f" · {len(_refunded)} refunded" if _refunded else "")
)

_collected = sum(r["total_price"] or 0 for r in _paid)
k2.metric("Collected", f"${_collected:,.2f}")
k2.caption("Paid orders only — includes shipping")

k3.metric("Traced to one ad", f"{len(_traced_ad)} of {len(_rows)}")
_k3_extra = []
if _traced_creative:
    _k3_extra.append(f"{len(_traced_creative)} to a creative")
if _traced_adset:
    _k3_extra.append(f"{len(_traced_adset)} to an ad-set only")
k3.caption(
    " · ".join(_k3_extra) if _k3_extra
    else "ad-level attribution needs the {{ad.id}} macro in Meta's URL parameters"
)

if _paid:
    _last_date = max(r["order_date"] for r in _paid)
    try:
        _days = (date.today() - date.fromisoformat(_last_date)).days
        k4.metric("Days since last paid order", f"{_days}")
        k4.caption(f"most recent: {_last_date}")
    except ValueError:
        k4.metric("Days since last paid order", "—")
        k4.caption(f"most recent: {_last_date}")
else:
    k4.metric("Days since last paid order", "—")
    k4.caption("no paid orders in scope")

st.divider()

# ---------------------------------------------------------------------------
# The orders table
# ---------------------------------------------------------------------------
def _touch_text(row: dict, prefix: str) -> str:
    """One readable line for a first/last visit: where they came from, then how."""
    utm_src = row.get(f"{prefix}_utm_source") or ""
    utm_camp = row.get(f"{prefix}_utm_campaign") or ""
    content = row.get(f"{prefix}_utm_content") or ""
    src = row.get(f"{prefix}_source") or ""
    parts: list[str] = []
    if utm_src:
        parts.append(f"{utm_src}{' / ' + utm_camp if utm_camp else ''}")
    elif src and src not in ("direct",):
        # `source` is a URL for referral traffic; the host is the useful part.
        host = src.split("//")[-1].split("/")[0]
        parts.append(host or src)
    elif src == "direct":
        parts.append("direct — no referrer")
    if content and not db._looks_like_meta_id(content):
        parts.append(f"lp={content}")
    return " · ".join(parts) or "no source captured"


def _short_src(value: str) -> str:
    """Collapse a source to something readable in prose.

    Shopify stores a full URL for referral traffic, and for some test orders that
    URL is a whole checkout permalink — printing it verbatim in a note both wraps
    badly and gets auto-linked by Streamlit.
    """
    v = str(value or "").strip()
    if not v:
        return "?"
    if v.startswith("http"):
        host = v.split("//")[-1].split("/")[0]
        return host or v[:40]
    return v


def _ad_text(row: dict) -> str:
    if row["traced"] == "ad" and row["ad_name"]:
        return row["ad_name"]
    if row["ad_id"]:
        return f"ad {row['ad_id']} (name not in ad_creatives)"
    if row["traced"] == "creative":
        n = len(row["creative_ad_names"])
        return f"creative {row['creative_code']} — {n} ads share it, not one ad"
    if row["adset_id"]:
        return f"ad-set {row['adset_id']} — ad-set level only"
    return "—"


_df = pd.DataFrame([
    {
        "Order": r["order_name"],
        "Day": r["order_date"],
        "Status": r["financial_status"],
        "Value": r["total_price"],
        "Visits": r["moments_count"],
        "First touch": _touch_text(r, "first"),
        "Last touch": _touch_text(r, "last"),
        "Ad traced": _ad_text(r),
    }
    for r in _rows
])
st.dataframe(
    _df,
    hide_index=True,
    use_container_width=True,
    column_config={
        "Order": st.column_config.TextColumn("Order", width="small"),
        "Day": st.column_config.TextColumn("Day", width="small"),
        "Status": st.column_config.TextColumn("Status", width="small"),
        "Value": st.column_config.NumberColumn("Value", format="$%.2f"),
        "Visits": st.column_config.NumberColumn(
            "Visits", format="%d",
            help="Shopify momentsCount — how many separate visits the journey "
                 "records. More than 1 means a multi-touch path is visible."),
        "First touch": st.column_config.TextColumn("First touch", width="medium"),
        "Last touch": st.column_config.TextColumn(
            "Last touch (before checkout)", width="medium"),
        "Ad traced": st.column_config.TextColumn("Ad traced", width="medium"),
    },
)

st.caption(
    "**How the traceback works — and why most rows show no ad.** Three tagging "
    "generations reach these order records and `utm_content` means something "
    "different in each:\n\n"
    "1. **Numeric ad id** (`utm_content=1202471348…`, `utm_term=<ad-set id>`, "
    "`utm_campaign=nowa-pre-order-img`). These ids are on **no** ad destination URL "
    "in `ad_creatives`, so they are not hardcoded in the link — they come from "
    "Meta's own ad-level **URL parameters** field via the `{{ad.id}}` / "
    "`{{adset.id}}` macros, which Meta appends at click time. Only this generation "
    "pins one specific ad.\n"
    "2. **Creative code** (`utm_content=HOME-08`, `utm_term=homepage|preorder-lp`). "
    "This is what every current destination URL actually carries. The code appears "
    "inside `ad_name`, so it resolves to a *creative* — and since several ad-sets "
    "reuse a code, it only counts as an ad-level trace when exactly one ad carries "
    "it.\n"
    "3. **Page slug** (`utm_content=home`). Oldest generation — identifies the "
    "landing page only.\n\n"
    "One order can mix generations. Attribution here uses **last touch** for the ad "
    "column, because that is the click that delivered the buyer; the first-touch "
    "column is where the journey started."
)

st.divider()

# ---------------------------------------------------------------------------
# So what — derived from the rows above, not hand-written
# ---------------------------------------------------------------------------
# Every bullet is computed. Hard-coding this week's narrative would go stale the
# next time an order lands, and a stale "so what" is worse than none.
st.subheader("So what — and what to do next")

_notes: list[str] = []

if _traced_ad:
    _names = {r["ad_name"] or r["ad_id"] for r in _traced_ad}
    _notes.append(
        f"**Ad-level attribution is working on {len(_traced_ad)} order(s).** "
        f"Resolved to: {', '.join(sorted(n for n in _names if n))}. "
        "Proof that an ad id survives all the way into the Shopify order record — "
        "but note it arrived via Meta's ad-level **URL parameters** field, not via "
        "the destination URLs, which still tag `utm_content` with a creative code. "
        "Put the `{{ad.id}}` macro on every ad-set's URL parameters and every future "
        "order becomes traceable to one ad instead of to a shared creative."
    )
else:
    _notes.append(
        "**No order is traceable to a single ad yet.** The current destination URLs "
        "tag `utm_content` with a creative code (HOME-08), which several ad-sets "
        "share — so it names a creative, not an ad. To pin one ad, add "
        "`utm_content={{ad.id}}` in Meta's ad-level **URL parameters** field "
        "(Ads Manager → Tracking); that is the only generation that has ever "
        "produced a single-ad trace here."
    )

if _multi_touch:
    _paths = []
    for r in _multi_touch:
        f = r["first_utm_content"] or _short_src(r["first_source"])
        last = r["last_utm_content"] or _short_src(r["last_source"])
        if f != last:
            _paths.append(f"{r['order_name']}: {f} → {last}")
    if _paths:
        _notes.append(
            f"**{len(_paths)} order(s) show a real multi-touch path** — "
            + "; ".join(_paths[:4])
            + ". Shopify's last-touch model credits only the final page, so a segment "
            "LP that introduces a buyer shows up as a homepage order. Judge segment "
            "pages on this column, not on their order count."
        )

if _no_source:
    _notes.append(
        f"**{len(_no_source)} order(s) captured no source at all.** Direct, dark "
        "social, an email click, or a stripped referrer — indistinguishable after "
        "the fact. If they cluster before your UTM scheme went live, the gap is "
        "closed; if they are recent, something is dropping the parameters."
    )

if _pending:
    _stuck = ", ".join(
        f"{r['order_name']} ({r['order_date']}, \\${r['total_price'] or 0:,.0f})"
        for r in _pending
    )
    _notes.append(
        f"**{len(_pending)} order(s) still pending: {_stuck}.** A stuck payment or an "
        "abandoned wallet authorisation is recoverable in a way an abandoned cart is "
        "not — these are counted in Orders but not in Collected. Worth a manual look."
    )

if _refunded:
    _notes.append(
        f"**{len(_refunded)} order(s) refunded.** Excluded from Collected. Check "
        "whether these were tests or real cancellations before reading the paid count "
        "as demand."
    )

# Same-day repeats are the cheapest repeat-purchase signal available here.
_by_day: dict[str, int] = {}
for r in _paid:
    _by_day[r["order_date"]] = _by_day.get(r["order_date"], 0) + 1
_multi_days = {d: n for d, n in _by_day.items() if n > 1}
if _multi_days:
    _notes.append(
        "**More than one paid order on the same day: "
        + ", ".join(f"{d} ({n})" for d, n in sorted(_multi_days.items()))
        + ".** Could be a repeat buyer (two devices, or one per child) or simply a "
        "good day — check the journeys above before treating it as a repeat rate."
    )

if _paid:
    try:
        _gap = (date.today() - date.fromisoformat(max(r["order_date"] for r in _paid))).days
        if _gap >= 3:
            _notes.append(
                f"**{_gap} days since the last paid order.** Cross-check the funnel "
                "page: if `/preorder` traffic held while checkouts fell, the problem "
                "is the Reserve step, not price or checkout UX."
            )
    except ValueError:
        pass

for note in _notes:
    st.markdown(f"- {note}")

st.caption(
    "Every bullet above is computed from the table, so it stays true as orders "
    "land — there is no hand-written narrative here to go stale."
)
