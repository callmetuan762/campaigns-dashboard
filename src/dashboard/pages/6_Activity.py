"""Activity Log page — Meta change_history feed (DASH-14).

Shows campaign, ad set, and ad changes pulled from Meta's change history API.
The daily backfill at 03:00 fetches the last 7 days of change events.

Standalone: no aiogram / no src.ai / no src.bot imports (Phase 6 D-19 rule).
"""
from __future__ import annotations

import html
import json
from datetime import date, timedelta
from typing import Any

import streamlit as st

st.set_page_config(
    page_title="Activity Log",
    layout="wide",
    initial_sidebar_state="expanded",
)

from src.dashboard import db                          # noqa: E402
from src.dashboard.settings import DashboardSettings  # noqa: E402

# Dark-theme palette -- duplicated from app.py per D-19 standalone rule
COLOR_BG_PAPER = "#0f1117"
COLOR_BG_PLOT = "#1a1d27"
COLOR_FONT = "#e4e7ef"
COLOR_GRID = "#2a2e3a"
COLOR_SPEND = "rgba(99, 125, 255, 0.6)"
COLOR_DEPOSITS = "#34d399"
COLOR_META = "#60a5fa"
COLOR_GA4 = "#a78bfa"
COLOR_CPD = "#f59e0b"

# Event type colors (markdown/HTML labels)
_EVENT_COLOR = {
    "CREATE": "#34d399",  # green
    "UPDATE": "#f59e0b",  # yellow/amber
    "DELETE": "#f87171",  # red
}

# Object type colors
_OBJECT_COLOR = {
    "CAMPAIGN": "#60a5fa",  # blue
    "AD_SET": "#fb923c",    # orange
    "AD": "#4ade80",        # green
}

# Fields worth showing a concise old→new diff for
_DIFF_FIELDS = {"status", "budget_remaining", "daily_budget", "name", "bid_amount", "lifetime_budget"}

# Human-readable event type labels
_EVENT_LABELS: dict[str, str] = {
    "update_ad_run_status": "Status changed",
    "update_ad_set_run_status": "Status changed",
    "update_campaign_run_status": "Status changed",
    "update_ad_run_status_to_be_set_after_review": "Pending review",
    "create_ad": "Ad created",
    "create_ad_set": "Ad set created",
    "create_campaign_group": "Campaign created",
    "update_ad_creative": "Creative updated",
    "update_ad_set_target_spec": "Targeting updated",
    "update_ad_set_bid_strategy": "Bid strategy updated",
    "update_ad_set_budget": "Budget updated",
    "update_campaign_budget": "Budget updated",
    "update_campaign_name": "Name updated",
    "update_ad_name": "Name updated",
    "delete_ad": "Ad deleted",
    "delete_ad_set": "Ad set deleted",
}


def _check_auth(password_required: str) -> bool:
    if not password_required:
        return True
    if st.session_state.get("authenticated"):
        return True
    st.title("Activity Log")
    st.caption("Sign in to continue.")
    with st.form("auth_form_activity"):
        pw = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
        if submitted:
            if pw == password_required:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password")
    return False


def _fmt_timestamp(ts: str) -> str:
    """Format an ISO datetime string to a compact human-readable form."""
    try:
        # Handles both '2026-05-24T14:32:00+0000' and '2026-05-24 14:32:00' forms
        ts_clean = ts.replace("T", " ").split("+")[0].split("Z")[0].strip()
        dt_part = ts_clean[:16]  # 'YYYY-MM-DD HH:MM'
        d, t = dt_part.split(" ")
        year, month, day = d.split("-")
        month_name = date(int(year), int(month), int(day)).strftime("%b")
        return f"{month_name} {int(day)} {t}"
    except Exception:
        return ts[:16] if len(ts) >= 16 else ts


def _fmt_date_header(ts: str) -> str:
    """Return a readable date label like 'May 24, 2026' from an ISO datetime string."""
    try:
        ts_clean = ts.replace("T", " ").split("+")[0].split("Z")[0].strip()
        d = ts_clean[:10]  # 'YYYY-MM-DD'
        parsed = date.fromisoformat(d)
        return parsed.strftime("%B %d, %Y").replace(" 0", " ")
    except Exception:
        return ts[:10] if len(ts) >= 10 else ts


def _date_key(ts: str) -> str:
    """Extract YYYY-MM-DD from a timestamp string."""
    try:
        ts_clean = ts.replace("T", " ").split("+")[0].split("Z")[0].strip()
        return ts_clean[:10]
    except Exception:
        return ts[:10] if len(ts) >= 10 else ts


def _colored_badge(text: str, color: str) -> str:
    """Return inline HTML for a colored badge."""
    return (
        f'<span style="background-color:{color}22;color:{color};'
        f'border:1px solid {color}55;border-radius:4px;'
        f'padding:1px 7px;font-size:0.78rem;font-weight:600;">'
        f'{text}</span>'
    )


def _parse_changed_fields(raw: str | None) -> str:
    """Parse the changed_fields JSON blob into a comma-separated string."""
    if not raw:
        return ""
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return ", ".join(str(v) for v in val)
        return str(val)
    except (json.JSONDecodeError, TypeError):
        return str(raw)


def _build_diff_text(changed_fields_raw: str | None, old_raw: str | None, new_raw: str | None) -> str:
    """Build a compact diff string.

    Handles three cases:
    - Simple string values: 'Pending Review → Active'
    - Dict values with known fields: 'status: active → paused', 'daily_budget: $100 → $150'
    - Unknown fields: just list the field names that changed
    """
    changed: list[str] = []
    try:
        if changed_fields_raw:
            cf = json.loads(changed_fields_raw)
            if isinstance(cf, list):
                changed = [str(f) for f in cf]
    except (json.JSONDecodeError, TypeError):
        pass

    old_parsed: Any = None
    new_parsed: Any = None
    try:
        if old_raw:
            old_parsed = json.loads(old_raw)
    except (json.JSONDecodeError, TypeError):
        old_parsed = old_raw
    try:
        if new_raw:
            new_parsed = json.loads(new_raw)
    except (json.JSONDecodeError, TypeError):
        new_parsed = new_raw

    # Case 1: simple string values (e.g. status change returns bare strings)
    if isinstance(old_parsed, str) and isinstance(new_parsed, str) and old_parsed != new_parsed:
        return f"{old_parsed} → {new_parsed}"

    old_dict = old_parsed if isinstance(old_parsed, dict) else {}
    new_dict = new_parsed if isinstance(new_parsed, dict) else {}

    parts = []
    for field in changed:
        if field in _DIFF_FIELDS and (old_dict or new_dict):
            old_v = old_dict.get(field, "?")
            new_v = new_dict.get(field, "?")
            if field in ("budget_remaining", "daily_budget", "bid_amount", "lifetime_budget"):
                # Meta returns budget in cents
                try:
                    old_v = f"${float(old_v) / 100:,.2f}"
                except (TypeError, ValueError):
                    old_v = str(old_v)
                try:
                    new_v = f"${float(new_v) / 100:,.2f}"
                except (TypeError, ValueError):
                    new_v = str(new_v)
            parts.append(f"{field}: {old_v} → {new_v}")
        elif field not in _DIFF_FIELDS:
            parts.append(field)

    if not parts and changed:
        parts = changed

    return " | ".join(parts)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_changelog(
    db_path_str: str,
    start: str,
    end: str,
    object_types_key: str,
) -> list[dict[str, Any]]:
    from pathlib import Path
    otypes = object_types_key.split(",") if object_types_key else None
    return db.get_changelog_entries(Path(db_path_str), start, end, otypes)


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------
settings = DashboardSettings()  # type: ignore[call-arg]

if not _check_auth(settings.dashboard_password):
    st.stop()

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
st.title("Activity Log")
st.caption("Major changes to campaigns, ad sets and ads pulled from Meta's change history API.")

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
_PAGE_SIZE = 50  # entries rendered per page

with st.sidebar:
    st.header("Filters")
    today = date.today()
    default_end = today - timedelta(days=1)
    default_start = default_end - timedelta(days=6)  # 7-day default (30d was too slow)

    dates = st.date_input(
        "Date range",
        value=(default_start, default_end),
        max_value=today,
        key="activity_date_range",
    )
    if isinstance(dates, tuple) and len(dates) == 2:
        start_date, end_date = dates
    else:
        st.warning("Pick both a start and an end date.")
        st.stop()

    object_type_options = ["CAMPAIGN", "AD_SET", "AD"]
    selected_types = st.multiselect(
        "Object types",
        options=object_type_options,
        default=object_type_options,
        key="activity_object_types",
    )

    st.divider()
    refresh = st.button("Refresh", use_container_width=True)
    if refresh:
        st.cache_data.clear()

# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------
db_path_str = str(settings.db_path)
otypes_key = ",".join(sorted(selected_types)) if selected_types else ""

entries = _cached_changelog(
    db_path_str,
    start_date.isoformat(),
    end_date.isoformat(),
    otypes_key,
)

# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------
if not entries:
    st.info(
        "No changelog data yet. The daily backfill at 03:00 fetches the last 7 days. "
        "You can also run: `python -m src.backfill` to populate."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Summary + pagination state
# ---------------------------------------------------------------------------
from itertools import groupby  # noqa: E402

def _date_sort_key(e: dict) -> str:
    return _date_key(e.get("change_time", ""))

entries_sorted = sorted(entries, key=_date_sort_key, reverse=True)
total = len(entries_sorted)
total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)

# Keep current page in session state; reset if filter params changed
_filter_key = f"{start_date}|{end_date}|{otypes_key}"
if st.session_state.get("_activity_filter_key") != _filter_key:
    st.session_state["_activity_page"] = 0
    st.session_state["_activity_filter_key"] = _filter_key

current_page: int = st.session_state.get("_activity_page", 0)
current_page = max(0, min(current_page, total_pages - 1))

page_start = current_page * _PAGE_SIZE
page_end = min(page_start + _PAGE_SIZE, total)
page_entries = entries_sorted[page_start:page_end]

st.markdown(
    f"**{total:,} events** in range {start_date.isoformat()} – {end_date.isoformat()} "
    f"· showing {page_start + 1}–{page_end}"
)
st.divider()

# ---------------------------------------------------------------------------
# Timeline — current page only
# ---------------------------------------------------------------------------
for date_str, group_iter in groupby(page_entries, key=_date_sort_key):
    group = list(group_iter)

    # Date divider header
    try:
        parsed_date = date.fromisoformat(date_str)
        # strftime %-d not available on Windows — use %d + lstrip workaround
        friendly_date = parsed_date.strftime("%B %d, %Y").replace(" 0", " ")
    except ValueError:
        friendly_date = date_str

    st.markdown(f"### {friendly_date}")

    for entry in group:
        ts = entry.get("change_time", "")
        obj_type = entry.get("object_type", "")
        obj_name = entry.get("object_name", "")
        event_type = entry.get("event_type", "")
        changed_fields_raw = entry.get("changed_fields")
        old_raw = entry.get("old_value")
        new_raw = entry.get("new_value")
        actor = entry.get("actor_name", "")

        obj_color = _OBJECT_COLOR.get(obj_type, COLOR_FONT)
        if event_type.startswith("create"):
            evt_color = _EVENT_COLOR["CREATE"]
        elif event_type.startswith("delete"):
            evt_color = _EVENT_COLOR["DELETE"]
        else:
            evt_color = _EVENT_COLOR["UPDATE"]

        type_badge = _colored_badge(obj_type, obj_color)
        event_label = _EVENT_LABELS.get(event_type, event_type.replace("_", " ").title())
        event_badge = _colored_badge(event_label, evt_color)

        changed_str = _parse_changed_fields(changed_fields_raw)
        diff_text = _build_diff_text(changed_fields_raw, old_raw, new_raw)

        with st.container():
            col_ts, col_badges, col_name, col_detail = st.columns([1.2, 1.5, 2.5, 4])

            with col_ts:
                st.markdown(
                    f'<span style="color:#888;font-size:0.82rem;">{_fmt_timestamp(ts)}</span>',
                    unsafe_allow_html=True,
                )
            with col_badges:
                st.markdown(f"{type_badge} {event_badge}", unsafe_allow_html=True)
            with col_name:
                st.markdown(
                    f'<span style="font-size:0.9rem;font-weight:500;">{html.escape(obj_name or "")}</span>',
                    unsafe_allow_html=True,
                )
            with col_detail:
                detail_parts = []
                if diff_text:
                    detail_parts.append(html.escape(diff_text))
                elif changed_str:
                    detail_parts.append(f"fields: {html.escape(changed_str)}")
                if actor:
                    detail_parts.append(f"by {html.escape(actor)}")
                detail = " · ".join(detail_parts) if detail_parts else ""
                st.markdown(
                    f'<span style="color:#aaa;font-size:0.82rem;">{detail}</span>',
                    unsafe_allow_html=True,
                )

    st.divider()

# ---------------------------------------------------------------------------
# Pagination controls
# ---------------------------------------------------------------------------
if total_pages > 1:
    pg_left, pg_mid, pg_right = st.columns([1, 2, 1])
    with pg_left:
        if st.button("← Previous", disabled=current_page == 0, use_container_width=True):
            st.session_state["_activity_page"] = current_page - 1
            st.rerun()
    with pg_mid:
        st.markdown(
            f'<p style="text-align:center;color:#888;padding-top:8px;">'
            f'Page {current_page + 1} of {total_pages}</p>',
            unsafe_allow_html=True,
        )
    with pg_right:
        if st.button("Next →", disabled=current_page >= total_pages - 1, use_container_width=True):
            st.session_state["_activity_page"] = current_page + 1
            st.rerun()
