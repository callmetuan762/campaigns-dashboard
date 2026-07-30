"""Google Sheets client for the Preorder Leads Dashboard (email leads).

Separate spreadsheet from the legacy stripe_payments one in ``client.py`` — set
``GOOGLE_SHEETS_LEADS_SPREADSHEET_ID`` and share the sheet with the same service
account email already used for GA4.

The sheet has no stable tab *names* we can rely on, so tabs are identified by
their header signature (``classify_header``) rather than by title or index. Two
tabs — preorder-started and exit-intent — have byte-identical headers, so those
are told apart by their ``Reason`` column contents: exit-intent rows are
consistently prefixed ``exit-intent ·`` while the preorder-started tab's Reason
column holds mixed values (an LP slug, a campaign id, or ``/preorder``).

Rows are read with ``get_all_values()``, not ``get_all_records()``: the sheet's
summary tab has merged/blank header cells and gspread raises
``the header row in the worksheet contains duplicates: ['']`` on those — the
exact failure already seen in production against the stripe sheet.

The parsing is split into pure functions (``classify_header``,
``build_lead_records``) and a thin I/O wrapper (``fetch_email_leads``) so the
normalisation can be tested against a captured snapshot without network access.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

import gspread
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = structlog.get_logger(__name__)

# Tab kinds returned by classify_header().
TAB_QUIZ = "quiz"
TAB_PREORDER_STARTED = "preorder_started"
TAB_EXIT_INTENT = "exit_intent"
TAB_EVENTS = "events"
TAB_DEDUP = "dedup"

# The sheet writes timestamps in two different shapes; see _parse_ts_utc.
_GMT7_OFFSET = timedelta(hours=7)


def _norm(s: str) -> str:
    """Lowercase + collapse whitespace, for tolerant header matching."""
    return " ".join(str(s or "").split()).lower()


def classify_header(header: Iterable[str], reason_values: Iterable[str] = ()) -> str | None:
    """Identify which lead tab a worksheet is, from its header row.

    ``reason_values`` is only consulted for the two tabs whose headers are
    identical (preorder-started vs exit-intent). Returns None for tabs that
    carry no lead rows (the summary dashboard, the field-spec tab).
    """
    cols = {_norm(c) for c in header if str(c or "").strip()}
    if not cols:
        return None

    has_ts = "timestamp" in cols
    has_email = "email" in cols

    # Deduped Leads tab: one row per email, no timestamp, carries the running
    # Shopify totals. Checked first — it is the only lead tab without Timestamp.
    if has_email and not has_ts and "total shopify order (so far)" in cols:
        return TAB_DEDUP

    if not (has_ts and has_email):
        return None

    # Raw event log: same status column as the deduped tab, plus a cart token.
    if "cart token" in cols:
        return TAB_EVENTS

    # Field-spec / example tab shares most of the quiz header but documents the
    # AC tag instead of the AC sync status. Excluded before the quiz check.
    if "ac tag" in cols or "notes / source" in cols:
        return None

    if "quiz" in cols and "result type" in cols:
        return TAB_QUIZ

    if "reason" in cols:
        reasons = [_norm(r) for r in reason_values if str(r or "").strip()]
        if reasons and sum(r.startswith("exit-intent") for r in reasons) * 2 >= len(reasons):
            return TAB_EXIT_INTENT
        return TAB_PREORDER_STARTED

    return None


def _parse_ts_utc(raw: str) -> datetime | None:
    """Parse either timestamp shape the sheet uses into an aware UTC datetime.

    - ``2026-07-14T20:49:43.116Z`` — machine-written ISO, already UTC.
    - ``07/13/2026 14:35`` — MM/DD/YYYY HH:MM in GMT+7 local time (the event
      log), converted to UTC here so every stored date shares one basis.

    Returns None for blanks or anything unrecognised, so a malformed cell
    downgrades that one row rather than failing the whole pull.
    """
    s = str(raw or "").strip()
    if not s:
        return None

    if "T" in s:
        cleaned = s.replace("T", " ")
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1]
        if "." in cleaned:
            cleaned = cleaned.split(".")[0]
        try:
            return datetime.strptime(cleaned[:19], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None

    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M"):
        try:
            local = datetime.strptime(s, fmt)
        except ValueError:
            continue
        return (local - _GMT7_OFFSET).replace(tzinfo=timezone.utc)

    # Fall back to an ISO-ish 'YYYY-MM-DD HH:MM' written without the T.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_money(raw: str) -> float | None:
    """'$116', '1,044', '' -> 116.0, 1044.0, None."""
    s = str(raw or "").strip().replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int(raw: str) -> int | None:
    v = _parse_money(raw)
    return int(v) if v is not None else None


def is_internal_email(email: str, patterns: Iterable[str]) -> bool:
    """True when the address matches any configured internal/test substring."""
    e = _norm(email)
    return any(p for p in (_norm(p) for p in patterns) if p and p in e)


def _rows_as_dicts(header: list[str], rows: list[list[str]]) -> list[dict[str, str]]:
    """Zip a header row against data rows, tolerating short/ragged rows."""
    keys = [_norm(c) for c in header]
    out: list[dict[str, str]] = []
    for row in rows:
        if not any(str(c or "").strip() for c in row):
            continue
        rec = {}
        for i, key in enumerate(keys):
            if not key:
                continue
            rec[key] = str(row[i]).strip() if i < len(row) else ""
        out.append(rec)
    return out


def build_lead_records(
    tabs: dict[str, tuple[list[str], list[list[str]]]],
    internal_patterns: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Collapse the sheet's tabs into one deduped row per email.

    ``tabs`` maps a tab kind (the TAB_* constants) to ``(header, rows)``.

    Grain is one row per email. The email set is the *union* of every
    lead-bearing tab, not just the deduped Leads tab: a live snapshot had 17
    addresses present in the channel tabs but missing from the deduped tab, and
    dropping them here would lose them for good. They are kept with
    ``in_dedup_tab = 0`` so dashboard totals can still filter down to the
    sheet's own headline population (which reproduces its reported 156) while
    the extras stay auditable. The deduped tab is authoritative for
    ``deposit_status`` and the Shopify totals.

    ``first_seen_at`` is the earliest timestamp seen for that email anywhere,
    which is what makes the deduped rows date-filterable at all.
    """
    patterns = list(internal_patterns)
    leads: dict[str, dict[str, Any]] = {}

    def _slot(email: str) -> dict[str, Any] | None:
        key = _norm(email)
        if not key or "@" not in key:
            return None
        if key not in leads:
            leads[key] = {
                "email": key,
                "first_seen": None,
                "deposit_status": "",
                "quiz_name": "",
                "quiz_type": "",
                "quiz_lp_url": "",
                "shopify_order_id": "",
                "shopify_order_value": None,
                "total_orders": None,
                "total_value": None,
                "from_quiz": 0,
                "from_preorder_started": 0,
                "from_exit_intent": 0,
                "in_dedup_tab": 0,
            }
        return leads[key]

    def _note_ts(slot: dict[str, Any], raw: str) -> None:
        ts = _parse_ts_utc(raw)
        if ts is None:
            return
        if slot["first_seen"] is None or ts < slot["first_seen"]:
            slot["first_seen"] = ts

    # --- channel tabs: identity + timestamps -------------------------------
    for kind, flag in (
        (TAB_QUIZ, "from_quiz"),
        (TAB_PREORDER_STARTED, "from_preorder_started"),
        (TAB_EXIT_INTENT, "from_exit_intent"),
    ):
        if kind not in tabs:
            continue
        header, rows = tabs[kind]
        for rec in _rows_as_dicts(header, rows):
            slot = _slot(rec.get("email", ""))
            if slot is None:
                continue
            slot[flag] = 1
            _note_ts(slot, rec.get("timestamp", ""))
            if kind == TAB_QUIZ:
                slot["quiz_name"] = slot["quiz_name"] or rec.get("quiz", "")
                slot["quiz_type"] = slot["quiz_type"] or rec.get("result type", "")
                slot["quiz_lp_url"] = slot["quiz_lp_url"] or rec.get("lp url", "")

    # --- raw event log: timestamps only (its Event type column is empty in
    # practice, so it cannot attribute a channel) --------------------------
    if TAB_EVENTS in tabs:
        header, rows = tabs[TAB_EVENTS]
        for rec in _rows_as_dicts(header, rows):
            slot = _slot(rec.get("email", ""))
            if slot is None:
                continue
            _note_ts(slot, rec.get("timestamp", ""))

    # --- deduped Leads tab: authoritative status + Shopify totals ----------
    if TAB_DEDUP in tabs:
        header, rows = tabs[TAB_DEDUP]
        for rec in _rows_as_dicts(header, rows):
            slot = _slot(rec.get("email", ""))
            if slot is None:
                continue
            slot["in_dedup_tab"] = 1
            slot["deposit_status"] = rec.get("deposit status (latest)", "")
            slot["quiz_name"] = slot["quiz_name"] or rec.get("quizname", "")
            slot["quiz_type"] = slot["quiz_type"] or rec.get("quiztype", "")
            slot["quiz_lp_url"] = slot["quiz_lp_url"] or rec.get("quizlpurl", "")
            slot["shopify_order_id"] = rec.get("shopify order id (latest)", "")
            slot["shopify_order_value"] = _parse_money(
                rec.get("shopify order value ($) latest", "")
            )
            slot["total_orders"] = _parse_int(rec.get("total shopify order (so far)", ""))
            slot["total_value"] = _parse_money(rec.get("total shopify value $ (so far)", ""))

    out: list[dict[str, Any]] = []
    undated = 0
    for slot in leads.values():
        ts: datetime | None = slot.pop("first_seen")
        if ts is None:
            # No parseable timestamp anywhere — cannot be date-filtered, so it
            # would silently distort every windowed count. Skipped, and counted
            # in the log so the drop is visible rather than invisible.
            undated += 1
            continue
        out.append(
            {
                **slot,
                "first_seen_at": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "lead_date": ts.strftime("%Y-%m-%d"),
                "is_internal": 1 if is_internal_email(slot["email"], patterns) else 0,
            }
        )

    if undated:
        logger.warning("leads_rows_without_timestamp", rows=undated)
    return out


@retry(
    retry=retry_if_exception_type(gspread.exceptions.APIError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
def fetch_email_leads(
    spreadsheet_id: str,
    client: gspread.Client,
    internal_patterns: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Read every lead tab from the sheet and return deduped per-email rows."""
    logger.info("leads_fetch_start", spreadsheet_id=spreadsheet_id)

    spreadsheet = client.open_by_key(spreadsheet_id)
    tabs: dict[str, tuple[list[str], list[list[str]]]] = {}

    for ws in spreadsheet.worksheets():
        values = ws.get_all_values()
        if not values:
            continue
        header, rows = values[0], values[1:]

        # Reason column contents are needed to separate the two identically
        # headed channel tabs; pull them before classifying.
        reason_values: list[str] = []
        keys = [_norm(c) for c in header]
        if "reason" in keys:
            idx = keys.index("reason")
            reason_values = [r[idx] for r in rows if len(r) > idx]

        kind = classify_header(header, reason_values)
        if kind is None:
            continue
        if kind in tabs:
            logger.warning("leads_duplicate_tab_kind", kind=kind, tab=ws.title)
            continue
        tabs[kind] = (header, rows)
        logger.info("leads_tab_matched", kind=kind, tab=ws.title, rows=len(rows))

    missing = {TAB_QUIZ, TAB_DEDUP} - set(tabs)
    if missing:
        logger.warning("leads_tabs_missing", missing=sorted(missing))

    rows_out = build_lead_records(tabs, internal_patterns)
    logger.info(
        "leads_fetch_done",
        spreadsheet_id=spreadsheet_id,
        emails=len(rows_out),
        external=sum(1 for r in rows_out if not r["is_internal"]),
        external_on_dedup_tab=sum(
            1 for r in rows_out if not r["is_internal"] and r["in_dedup_tab"]
        ),
    )
    return rows_out
