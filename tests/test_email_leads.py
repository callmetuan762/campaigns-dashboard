"""Unit tests for the email-leads path: sheet parsing, upsert, dashboard query.

Covers the parts of src/sheets/leads_client.py that are easy to get quietly
wrong — telling apart the two channel tabs that share an identical header row,
the two different timestamp shapes the sheet writes, and the dedup/flag rules —
plus the scoping contract of get_email_leads_summary.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.dashboard.db import get_email_leads_summary, get_internal_lead_emails
from src.sheets.leads_client import (
    TAB_DEDUP,
    TAB_EVENTS,
    TAB_EXIT_INTENT,
    TAB_PREORDER_STARTED,
    TAB_QUIZ,
    _parse_ts_utc,
    build_lead_records,
    classify_header,
    is_internal_email,
)
from tests.conftest import build_migrated_db

QUIZ_HEADER = [
    "Timestamp", "Email", "Quiz", "Result Type", "LP URL",
    "Deposit Status", "AC Status", "AC Error", "GMT +7", "Note for NOWA updates",
]
CHANNEL_HEADER = [
    "Timestamp", "Email", "AC Status", "AC Error", "GMT +7",
    "Reason", "Note for NOWA updates",
]
EVENTS_HEADER = [
    "Timestamp", "GMT+7", "Email", "Event type", "Deposit Status (Latest)",
    "QUIZNAME", "QUIZTYPE", "QUIZLPURL", "Shopify Order ID (Latest)",
    "Shopify Order Value ($)", "Cart token", "Notes",
]
DEDUP_HEADER = [
    "Email", "Deposit Status (Latest)", "QUIZNAME", "QUIZTYPE", "QUIZLPURL",
    "Shopify Order ID (Latest)", "Shopify Order Value ($) Latest",
    "Total Shopify Order (so far)", "Total Shopify Value $ (so far)",
]
SPEC_HEADER = [
    "Timestamp", "Email", "Quiz", "Result Type", "LP URL",
    "Deposit Status", "AC Tag", "Notes / Source",
]


# --- classify_header -------------------------------------------------------
def test_classify_header_identifies_each_lead_tab():
    assert classify_header(QUIZ_HEADER) == TAB_QUIZ
    assert classify_header(EVENTS_HEADER) == TAB_EVENTS
    assert classify_header(DEDUP_HEADER) == TAB_DEDUP


def test_classify_header_rejects_non_lead_tabs():
    """The summary dashboard (merged/blank header) and the field-spec tab carry
    no lead rows and must not be mistaken for the quiz tab they resemble."""
    assert classify_header(["", "", ""]) is None
    assert classify_header([]) is None
    assert classify_header(SPEC_HEADER) is None


def test_channel_tabs_with_identical_headers_split_on_reason_content():
    """Preorder-started and exit-intent share a byte-identical header, so the
    Reason column is the only discriminator: exit-intent rows are consistently
    prefixed 'exit-intent ·' while the preorder tab holds mixed values (an LP
    slug, a campaign id, '/preorder')."""
    exit_reasons = [
        "exit-intent · https://nowaplanet.com/?utm_source=fb",
        "exit-intent · https://nowaplanet.com/for/routine",
    ]
    preorder_reasons = ["/preorder", "routine", "120247349134050025", "big-feelings"]

    assert classify_header(CHANNEL_HEADER, exit_reasons) == TAB_EXIT_INTENT
    assert classify_header(CHANNEL_HEADER, preorder_reasons) == TAB_PREORDER_STARTED
    # No Reason values to judge by -> must not silently claim to be exit-intent.
    assert classify_header(CHANNEL_HEADER, []) == TAB_PREORDER_STARTED


# --- timestamp parsing -----------------------------------------------------
def test_parse_ts_utc_handles_iso_and_gmt7_shapes():
    iso = _parse_ts_utc("2026-07-14T20:49:43.116Z")
    assert iso is not None
    assert iso.strftime("%Y-%m-%d %H:%M:%S") == "2026-07-14 20:49:43"

    # The event log writes GMT+7 local time; 14:35 local is 07:35 UTC.
    local = _parse_ts_utc("07/13/2026 14:35")
    assert local is not None
    assert local.strftime("%Y-%m-%d %H:%M:%S") == "2026-07-13 07:35:00"


def test_parse_ts_utc_returns_none_on_junk():
    """A malformed cell must downgrade one row, not raise and kill the pull."""
    assert _parse_ts_utc("") is None
    assert _parse_ts_utc("   ") is None
    assert _parse_ts_utc("not a date") is None


def test_gmt7_conversion_can_move_a_lead_to_the_previous_day():
    """Early-morning GMT+7 rows belong to the previous UTC day; the whole point
    of converting at ingest is that both timestamp shapes end up on one basis."""
    ts = _parse_ts_utc("07/13/2026 06:00")
    assert ts is not None
    assert ts.strftime("%Y-%m-%d") == "2026-07-12"


# --- is_internal_email -----------------------------------------------------
@pytest.mark.parametrize(
    "email,expected",
    [
        ("huyle@resonancetech.co", True),
        ("someone@nowaplanet.com", True),
        ("HUYLE+1@ResonanceTech.co", True),  # case + plus-addressing
        ("parent@gmail.com", False),
    ],
)
def test_is_internal_email(email, expected):
    patterns = ["resonancetech.co", "nowaplanet.com"]
    assert is_internal_email(email, patterns) is expected


def test_default_patterns_catch_the_whole_internal_set():
    """Locks in the shipped default so a future edit cannot quietly let staff or
    test addresses back into the reported numbers. These are the real addresses
    found on the sheet, grouped as the sheet itself describes them."""
    from src.config import Settings

    patterns = [
        p.strip()
        for p in Settings.model_fields["leads_internal_email_patterns"].default.split(",")
        if p.strip()
    ]

    internal = [
        # @resonancetech.co, including plus-addressed variants
        "admin@resonancetech.co", "amy@resonancetech.co", "andy@resonancetech.co",
        "huyle@resonancetech.co", "huyle+1@resonancetech.co", "huyle+2@resonancetech.co",
        "nghiatran@resonancetech.co", "tai@resonancetech.co",
        # @nowaplanet.* debug accounts
        "debug-e2e-prod-test@nowaplanet.com", "debug-e2e-v2-test@nowaplanet.com",
        "debug-e2e-v3-test@nowaplanet.com",
        # the 8 personal test accounts
        "parent@example.com", "thisisatest@testingthis.com",
        "tdnghia.sdh221@hcmut.edu.vn", "tradanghi1999chung@gmail.com",
        "tradanghi1999chuyennganh@gmail.com", "tradanghi1999try2@gmail.com",
        "tai@tester.com", "contact.taihoang@gmail.com",
    ]
    for email in internal:
        assert is_internal_email(email, patterns), f"{email} should be internal"

    # Real lead addresses from the same sheet must survive the filter.
    for email in (
        "cookiestorm5@gmail.com", "jovialkitten@gmail.com", "ebailey0612@gmail.com",
        "angiesegal75@gmail.com", "wilsonyasmine02@gmail.com", "sethg88@gmail.com",
        "ddt.williams14@gmail.com",
    ):
        assert not is_internal_email(email, patterns), f"{email} should be external"


def test_teammate_name_patterns_do_not_swallow_ordinary_english_handles():
    """The internal set includes three addresses belonging to one teammate, and the
    obvious way to catch them is a bare "tai" substring. That is a trap: "tai" sits
    inside words US parents put in handles, and the failure is silent — the lead
    count just reads low. Patterns must stay narrow enough to leave these alone."""
    from src.config import Settings

    patterns = [
        p.strip()
        for p in Settings.model_fields["leads_internal_email_patterns"].default.split(",")
        if p.strip()
    ]

    for email in (
        "mountainmama7@gmail.com", "retailtherapy@gmail.com", "detailedmom@gmail.com",
        "captainkate@gmail.com", "sustainablysarah@gmail.com", "britainlee@gmail.com",
        "certainlyjen@gmail.com", "fountainhouse@gmail.com",
    ):
        assert not is_internal_email(email, patterns), (
            f"{email} is a plausible real lead and must not be filtered out"
        )


def test_internal_addresses_are_flagged_not_dropped():
    """is_internal is a flag so the exclusion list can be retuned without a
    re-ingest; the row must still land in the table."""
    tabs = {
        TAB_DEDUP: (
            DEDUP_HEADER,
            [["amy@resonancetech.co", "paid", "", "", "", "1018", "$116", "1", "116"]],
        ),
        TAB_EVENTS: (
            EVENTS_HEADER,
            [["07/20/2026 10:00", "", "amy@resonancetech.co", "", "paid",
              "", "", "", "1018", "116", "", ""]],
        ),
    }
    rows = build_lead_records(tabs, ["resonancetech.co"])
    assert len(rows) == 1
    assert rows[0]["is_internal"] == 1


# --- build_lead_records ----------------------------------------------------
def test_build_lead_records_dedupes_and_flags_channels():
    """One address seen in two channel tabs collapses to a single row with both
    flags set, keeping the EARLIEST timestamp."""
    tabs = {
        TAB_QUIZ: (
            QUIZ_HEADER,
            [
                ["2026-07-20T10:00:00.000Z", "a@gmail.com", "routine", "crasher",
                 "https://nowaplanet.com/for/routine", "", "synced", "", "", ""],
            ],
        ),
        TAB_EXIT_INTENT: (
            CHANNEL_HEADER,
            [
                # Same address, EARLIER than the quiz row above.
                ["2026-07-18T09:00:00.000Z", "a@gmail.com", "synced", "", "",
                 "exit-intent · https://nowaplanet.com/", ""],
                ["2026-07-19T09:00:00.000Z", "b@gmail.com", "synced", "", "",
                 "exit-intent · https://nowaplanet.com/", ""],
            ],
        ),
        TAB_DEDUP: (
            DEDUP_HEADER,
            [
                ["a@gmail.com", "paid", "routine", "crasher",
                 "https://nowaplanet.com/for/routine", "1017", "$116", "1", "116"],
                ["b@gmail.com", "exit_intent", "", "", "", "", "", "0", "0"],
            ],
        ),
    }
    rows = {r["email"]: r for r in build_lead_records(tabs, ["resonancetech.co"])}

    assert len(rows) == 2
    a = rows["a@gmail.com"]
    assert a["from_quiz"] == 1
    assert a["from_exit_intent"] == 1
    assert a["from_preorder_started"] == 0
    assert a["lead_date"] == "2026-07-18"          # earliest wins
    assert a["deposit_status"] == "paid"           # dedup tab is authoritative
    assert a["shopify_order_value"] == 116.0       # '$116' parsed
    assert a["total_orders"] == 1
    assert a["in_dedup_tab"] == 1
    assert a["is_internal"] == 0


def test_build_lead_records_marks_addresses_missing_from_dedup_tab():
    """Channel-only addresses are kept with in_dedup_tab = 0 rather than dropped,
    so the sheet-faithful total stays reproducible without losing the extras."""
    tabs = {
        TAB_QUIZ: (
            QUIZ_HEADER,
            [["2026-07-20T10:00:00.000Z", "ghost@gmail.com", "routine", "crasher",
              "", "", "synced", "", "", ""]],
        ),
        TAB_DEDUP: (DEDUP_HEADER, []),
    }
    rows = build_lead_records(tabs)
    assert len(rows) == 1
    assert rows[0]["in_dedup_tab"] == 0
    assert rows[0]["deposit_status"] == ""


def test_build_lead_records_drops_undated_and_malformed_addresses():
    """An address with no parseable timestamp anywhere cannot be date-filtered,
    so including it would distort every windowed count."""
    tabs = {
        TAB_DEDUP: (
            DEDUP_HEADER,
            [
                ["undated@gmail.com", "pending", "", "", "", "", "", "0", "0"],
                ["not-an-email", "pending", "", "", "", "", "", "0", "0"],
                ["", "pending", "", "", "", "", "", "0", "0"],
            ],
        ),
    }
    assert build_lead_records(tabs) == []


def test_events_tab_contributes_timestamps_only():
    """The event log's Event type column is empty in practice, so it can date an
    address but must never attribute a channel to it."""
    tabs = {
        TAB_EVENTS: (
            EVENTS_HEADER,
            [["07/13/2026 14:35", "", "c@gmail.com", "", "pending",
              "", "", "", "", "", "", ""]],
        ),
    }
    rows = build_lead_records(tabs)
    assert len(rows) == 1
    assert rows[0]["lead_date"] == "2026-07-13"
    assert rows[0]["from_quiz"] == 0
    assert rows[0]["from_preorder_started"] == 0
    assert rows[0]["from_exit_intent"] == 0


# --- get_email_leads_summary ----------------------------------------------
def _seed(path: Path, rows: list[tuple]) -> None:
    build_migrated_db(path)
    con = sqlite3.connect(str(path))
    con.executemany(
        "INSERT INTO email_leads (email, first_seen_at, lead_date, deposit_status, "
        "from_quiz, from_preorder_started, from_exit_intent, in_dedup_tab, is_internal) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()


def test_get_email_leads_summary_scopes_and_splits(tmp_path: Path):
    db = tmp_path / "leads.db"
    _seed(db, [
        # in window, on dedup tab, external
        ("a@x.com", "2026-07-22 10:00:00", "2026-07-22", "pending", 1, 0, 0, 1, 0),
        ("b@x.com", "2026-07-23 10:00:00", "2026-07-23", "paid", 1, 0, 1, 1, 0),
        ("c@x.com", "2026-07-24 10:00:00", "2026-07-24", "", 0, 1, 0, 1, 0),
        # excluded: internal
        ("d@resonancetech.co", "2026-07-24 10:00:00", "2026-07-24", "pending", 1, 0, 0, 1, 1),
        # excluded from total: channel-tab only
        ("e@x.com", "2026-07-25 10:00:00", "2026-07-25", "", 1, 0, 0, 0, 0),
        # excluded: outside window
        ("f@x.com", "2026-07-30 10:00:00", "2026-07-30", "pending", 1, 0, 0, 1, 0),
    ])

    s = get_email_leads_summary(db, "2026-07-22", "2026-07-28")
    assert s["total"] == 3
    assert s["channel_only"] == 1
    # Non-exclusive flags: b@x.com counts in both quiz and exit-intent, so the
    # three channel counts sum to 4 against a total of 3.
    assert s["from_quiz"] == 2
    assert s["from_exit_intent"] == 1
    assert s["from_preorder_started"] == 1
    # Blank status surfaces as an explicit bucket rather than vanishing.
    assert s["by_status"] == {"pending": 1, "paid": 1, "(not set)": 1}


def test_get_email_leads_summary_keeps_similar_statuses_apart(tmp_path: Path):
    """The live sheet contains both 'pending' and 'payment-pending'; collapsing
    them would hide a real data-entry inconsistency."""
    db = tmp_path / "leads.db"
    _seed(db, [
        ("a@x.com", "2026-07-22 10:00:00", "2026-07-22", "pending", 0, 0, 0, 1, 0),
        ("b@x.com", "2026-07-22 10:00:00", "2026-07-22", "payment-pending", 0, 0, 0, 1, 0),
    ])
    s = get_email_leads_summary(db, "2026-07-22", "2026-07-28")
    assert s["by_status"] == {"pending": 1, "payment-pending": 1}


def test_internal_exclusions_are_countable_and_listable(tmp_path: Path):
    """The excluded set must stay visible from the dashboard, so a newly-appearing
    staff address that the pattern list misses can actually be noticed."""
    db = tmp_path / "leads.db"
    _seed(db, [
        ("real@x.com", "2026-07-22 10:00:00", "2026-07-22", "pending", 1, 0, 0, 1, 0),
        ("amy@resonancetech.co", "2026-07-22 10:00:00", "2026-07-22", "paid", 1, 0, 0, 1, 1),
        ("huyle+1@resonancetech.co", "2026-07-23 10:00:00", "2026-07-23", "", 0, 0, 1, 0, 1),
        # internal but outside the window -> must not be listed
        ("old@resonancetech.co", "2026-07-01 10:00:00", "2026-07-01", "", 1, 0, 0, 1, 1),
    ])

    s = get_email_leads_summary(db, "2026-07-22", "2026-07-28")
    assert s["total"] == 1                 # only the real lead counts
    assert s["internal_excluded"] == 2     # both in-window internals, either tab

    listed = get_internal_lead_emails(db, "2026-07-22", "2026-07-28")
    assert [r["email"] for r in listed] == [
        "amy@resonancetech.co", "huyle+1@resonancetech.co",
    ]
    assert listed[0]["lead_date"] == "2026-07-22"


def test_get_internal_lead_emails_degrades_on_missing_table(tmp_path: Path):
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()
    assert get_internal_lead_emails(db, "2026-07-22", "2026-07-28") == []


def test_get_email_leads_summary_degrades_on_missing_table(tmp_path: Path):
    """Pre-migration DB / sheet not configured -> zeros, never an exception."""
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()
    s = get_email_leads_summary(db, "2026-07-22", "2026-07-28")
    assert s["total"] == 0
    assert s["by_status"] == {}
    assert s["channel_only"] == 0


# --- upsert idempotency ---------------------------------------------------
@pytest.mark.asyncio
async def test_upsert_email_leads_is_idempotent_and_keeps_earliest_date(db_client):
    """deposit_status moves forward on re-pull, but lead_date must not drift
    later just because the sheet logged a newer event for the same address."""
    row = {
        "email": "a@x.com", "first_seen_at": "2026-07-20 10:00:00",
        "lead_date": "2026-07-20", "deposit_status": "pending",
        "quiz_name": "routine", "quiz_type": "crasher", "quiz_lp_url": "",
        "shopify_order_id": "", "shopify_order_value": None,
        "total_orders": 0, "total_value": 0.0,
        "from_quiz": 1, "from_preorder_started": 0, "from_exit_intent": 0,
        "in_dedup_tab": 1, "is_internal": 0,
    }
    await db_client.upsert_email_leads([row])
    await db_client.upsert_email_leads([row])  # same pull twice -> still one row

    rows = await db_client.fetch_all("SELECT COUNT(*) AS n FROM email_leads")
    assert rows[0]["n"] == 1

    later = {**row, "first_seen_at": "2026-07-25 10:00:00",
             "lead_date": "2026-07-25", "deposit_status": "paid"}
    await db_client.upsert_email_leads([later])

    rows = await db_client.fetch_all(
        "SELECT lead_date, deposit_status FROM email_leads WHERE email = 'a@x.com'"
    )
    assert rows[0]["lead_date"] == "2026-07-20"     # earliest retained
    assert rows[0]["deposit_status"] == "paid"      # latest stage wins


# --- Initiate Checkout reconciliation (Meta vs GA4 vs Shopify) -------------
def _seed_checkout_recon(path: Path) -> None:
    build_migrated_db(path)
    con = sqlite3.connect(str(path))
    con.executescript("""
        INSERT INTO campaigns (id, source, name, status)
            VALUES ('c1', 'meta_ads', 'Nowa | SALES | x', 'ACTIVE');
        INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, meta_begin_checkout)
            VALUES ('c1', '2026-07-23', '', '', 32);
        INSERT INTO ga4_events (event_name, date, campaign_utm, lp_slug, event_count)
            VALUES ('begin_checkout', '2026-07-23', 'nowa', 'home', 66);
        INSERT INTO shopify_orders (order_id, created_at, order_date, total_price,
                                    financial_status)
            VALUES ('o1','2026-07-23T10:00:00Z','2026-07-23',116.0,'paid'),
                   ('o2','2026-07-24T10:00:00Z','2026-07-24',116.0,'paid');
        INSERT INTO shopify_checkouts (checkout_id, created_at, checkout_date, email)
            VALUES ('k1','2026-07-23T10:00:00Z','2026-07-23','a@x.com'),
                   ('k2','2026-07-24T10:00:00Z','2026-07-24','b@x.com'),
                   ('k3','2026-08-01T10:00:00Z','2026-08-01','c@x.com');
    """)
    con.commit()
    con.close()


def test_checkout_reconciliation_counts_each_source_separately(tmp_path: Path):
    """Three measurements of one step, never summed into a single figure."""
    from src.dashboard.db import get_checkout_reconciliation

    db = tmp_path / "recon.db"
    _seed_checkout_recon(db)
    r = get_checkout_reconciliation(db, "2026-07-22", "2026-07-28")

    assert r["meta"] == 32
    assert r["ga4"] == 66
    # Shopify is abandoned-with-email + paid orders, both inside the window;
    # the 1 Aug checkout is outside it and must not leak in.
    assert r["shopify_abandoned"] == 2
    assert r["shopify_orders"] == 2
    assert r["shopify"] == 4
    assert r["shopify_available"] is True


def test_checkout_reconciliation_flags_shopify_not_ingested(tmp_path: Path):
    """An empty checkouts table must read as "not ingested", not as a real zero —
    otherwise a missing pipeline looks like a week with no checkouts."""
    from src.dashboard.db import get_checkout_reconciliation

    db = tmp_path / "recon.db"
    build_migrated_db(db)
    r = get_checkout_reconciliation(db, "2026-07-22", "2026-07-28")
    assert r["shopify_available"] is False
    assert r["shopify"] == 0


def test_checkout_reconciliation_degrades_on_missing_table(tmp_path: Path):
    from src.dashboard.db import get_checkout_reconciliation

    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()
    r = get_checkout_reconciliation(db, "2026-07-22", "2026-07-28")
    assert r["meta"] == 0 and r["ga4"] == 0 and r["shopify_available"] is False


@pytest.mark.asyncio
async def test_upsert_shopify_checkouts_pins_created_date(db_client):
    """completed_at may change on re-pull; created_at/checkout_date must not, or a
    checkout would drift out of the period it belongs to."""
    row = {
        "checkout_id": "k1", "created_at": "2026-07-23T10:00:00Z",
        "checkout_date": "2026-07-23", "completed_at": None, "email": "a@x.com",
        "total_price": 116.0, "cart_token": "t1", "landing_site": "/", "lp_slug": "home",
    }
    await db_client.upsert_shopify_checkouts([row])
    await db_client.upsert_shopify_checkouts([
        {**row, "checkout_date": "2026-08-01", "completed_at": "2026-07-25T09:00:00Z"}
    ])

    rows = await db_client.fetch_all(
        "SELECT checkout_date, completed_at FROM shopify_checkouts WHERE checkout_id='k1'"
    )
    assert len(rows) == 1
    assert rows[0]["checkout_date"] == "2026-07-23"          # pinned
    assert rows[0]["completed_at"] == "2026-07-25T09:00:00Z"  # updated


# --- daily leads by status (time series) -----------------------------------
def test_leads_daily_by_status_is_scoped_like_the_headline(tmp_path: Path):
    """Daily rows must sum to Total leads — same internal/dedup-tab scoping — or
    the chart and the KPI above it would disagree."""
    from src.dashboard.db import get_email_leads_daily_by_status

    db = tmp_path / "leads.db"
    _seed(db, [
        ("a@x.com", "2026-07-22 10:00:00", "2026-07-22", "pending", 1, 0, 0, 1, 0),
        ("b@x.com", "2026-07-22 11:00:00", "2026-07-22", "pending", 1, 0, 0, 1, 0),
        ("c@x.com", "2026-07-23 10:00:00", "2026-07-23", "abandoned_checkout", 1, 0, 0, 1, 0),
        ("d@x.com", "2026-07-23 10:00:00", "2026-07-23", "", 1, 0, 0, 1, 0),
        # excluded: internal, channel-only, out of window
        ("e@resonancetech.co", "2026-07-23 10:00:00", "2026-07-23", "paid", 1, 0, 0, 1, 1),
        ("f@x.com", "2026-07-23 10:00:00", "2026-07-23", "paid", 1, 0, 0, 0, 0),
        ("g@x.com", "2026-07-30 10:00:00", "2026-07-30", "paid", 1, 0, 0, 1, 0),
    ])

    rows = get_email_leads_daily_by_status(db, "2026-07-22", "2026-07-28")
    got = {(r["lead_date"], r["deposit_status"]): r["count"] for r in rows}
    assert got == {
        ("2026-07-22", "pending"): 2,
        ("2026-07-23", "abandoned_checkout"): 1,
        ("2026-07-23", "(not set)"): 1,   # blank surfaces as its own series
    }

    summary = get_email_leads_summary(db, "2026-07-22", "2026-07-28")
    assert sum(r["count"] for r in rows) == summary["total"]


def test_leads_daily_by_status_degrades_on_missing_table(tmp_path: Path):
    from src.dashboard.db import get_email_leads_daily_by_status

    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()
    assert get_email_leads_daily_by_status(db, "2026-07-22", "2026-07-28") == []


# --- landing-page table: new funnel columns (migration 018) -----------------
def _seed_lp_table(path: Path) -> None:
    build_migrated_db(path)
    con = sqlite3.connect(str(path))
    con.executescript("""
        INSERT INTO campaigns (id, source, name, status)
            VALUES ('c1','meta_ads','Nowa | SALES | x','ACTIVE');
        -- ad-set grain (ad_id='') is what the table sums; link clicks present
        INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend,
                                impressions, clicks, inline_link_clicks)
            VALUES ('c1','2026-07-23','set_home','',100.0,1000,80,40);
        -- a second ad-set with link clicks NOT yet ingested (NULL)
        INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend,
                                impressions, clicks, inline_link_clicks)
            VALUES ('c1','2026-07-23','set_routine','',50.0,500,20,NULL);
        INSERT INTO ad_creatives (ad_id, ad_name, adset_id, destination_url)
            VALUES ('a1','home ad','set_home','https://nowaplanet.com/?utm_source=meta'),
                   ('a2','routine ad','set_routine','https://nowaplanet.com/for/routine/?x=1');
        INSERT INTO ga4_landing_pages (landing_page, date, sessions)
            VALUES ('/','2026-07-23',200), ('/?fbclid=abc','2026-07-23',50),
                   ('/for/routine/','2026-07-23',40);
        INSERT INTO ga4_events (event_name, date, campaign_utm, lp_slug, event_count)
            VALUES ('page_view_lp','2026-07-23','nowa','home',300),
                   ('cta_click_convert','2026-07-23','nowa','home',60),
                   ('page_view_lp','2026-07-23','nowa','routine',80),
                   ('cta_click_convert','2026-07-23','nowa','routine',8);
        -- (from == to) rows are each page's own sessions; the cross rows are the
        -- onward flow the table reads.
        INSERT INTO ga4_page_flow (date, from_slug, to_slug, sessions)
            VALUES ('2026-07-23','home','preorder',50),
                   ('2026-07-23','routine','preorder',10),
                   ('2026-07-23','preorder','preorder',158);
        INSERT INTO shopify_orders (order_id, created_at, order_date, total_price,
                                    financial_status, lp_slug)
            VALUES ('o1','2026-07-23T10:00:00Z','2026-07-23',116.0,'paid','home'),
                   ('o2','2026-07-23T11:00:00Z','2026-07-23',116.0,'paid','home'),
                   ('o3','2026-07-23T12:00:00Z','2026-07-23',116.0,'paid','preorder');
    """)
    con.commit()
    con.close()


def test_landing_page_table_derives_new_rates(tmp_path: Path):
    from src.dashboard.db import get_landing_page_table

    db = tmp_path / "lp.db"
    _seed_lp_table(db)
    rows = {r["lp_slug"]: r for r in get_landing_page_table(db, "2026-07-22", "2026-07-28")}

    home = rows["home"]
    assert home["impressions"] == 1000
    assert home["link_clicks"] == 40
    assert home["link_ctr_pct"] == 4.0          # 40/1000 — lower than CTR below
    assert home["ctr_pct"] == 8.0               # 80/1000, all clicks
    # ga4_landing_pages fragments by query string; '/' and '/?fbclid=abc' both
    # normalise to the same slug and must aggregate, not compete.
    assert home["sessions"] == 250
    assert home["cta_pct"] == 20.0              # 60 CTA / 300 LP views
    assert home["preorder_sessions"] == 50
    assert home["preorder_pct"] == 20.0         # 50 / 250 sessions
    assert home["orders"] == 2
    assert home["order_pct"] == 0.67            # 2 / 300 LP views


def test_link_ctr_is_none_when_field_not_ingested(tmp_path: Path):
    """A NULL inline_link_clicks means "this date predates the field", which must
    read as no data rather than as zero link clicks."""
    from src.dashboard.db import get_landing_page_table

    db = tmp_path / "lp.db"
    _seed_lp_table(db)
    rows = {r["lp_slug"]: r for r in get_landing_page_table(db, "2026-07-22", "2026-07-28")}
    assert rows["routine"]["link_clicks_available"] is False
    assert rows["routine"]["link_ctr_pct"] is None
    # ...while the all-clicks CTR is still available for that row.
    assert rows["routine"]["ctr_pct"] == 4.0


def test_preorder_row_has_no_onward_flow(tmp_path: Path):
    """Sessions that LANDED on /preorder did not travel there, so the offer page's
    own (from == to) flow row must not be counted as onward traffic — otherwise the
    row would claim 158 sessions "reached /preorder" that started there. The rate is
    None for the same reason: it would measure the row against itself."""
    from src.dashboard.db import get_landing_page_table

    db = tmp_path / "lp.db"
    _seed_lp_table(db)
    rows = {r["lp_slug"]: r for r in get_landing_page_table(db, "2026-07-22", "2026-07-28")}
    assert rows["preorder"]["preorder_sessions"] == 0
    assert rows["preorder"]["preorder_pct"] is None
    # ...while a real onward hop from another page is still counted.
    assert rows["home"]["preorder_sessions"] == 50


# --- quiz funnel table ------------------------------------------------------
def test_quiz_funnel_traces_two_hops(tmp_path: Path):
    """quiz page -> its segment LP -> /preorder, all three sharing one denominator."""
    from src.dashboard.db import get_quiz_funnel_table

    db = tmp_path / "quiz.db"
    build_migrated_db(db)
    con = sqlite3.connect(str(db))
    con.executescript("""
        INSERT INTO ga4_page_flow (date, from_slug, to_slug, sessions) VALUES
            ('2026-07-23','screen-kid','screen-kid',30),
            ('2026-07-23','screen-kid','screen-anxious',7),
            ('2026-07-23','screen-kid','preorder',5);
        INSERT INTO email_leads (email, first_seen_at, lead_date, quiz_name,
                                 in_dedup_tab, is_internal)
            VALUES ('a@x.com','2026-07-23 10:00:00','2026-07-23','screen-anxious',1,0),
                   ('b@x.com','2026-07-23 10:00:00','2026-07-23','screen-anxious',1,0),
                   ('i@resonancetech.co','2026-07-23 10:00:00','2026-07-23',
                    'screen-anxious',1,1);
        INSERT INTO shopify_orders (order_id, created_at, order_date, total_price,
                                    financial_status, lp_slug)
            VALUES ('o1','2026-07-23T10:00:00Z','2026-07-23',116.0,'paid','screen-anxious');
    """)
    con.commit()
    con.close()

    rows = {r["quiz_slug"]: r for r in get_quiz_funnel_table(db, "2026-07-22", "2026-07-28")}
    sk = rows["screen-kid"]
    assert sk["segment_slug"] == "screen-anxious"
    assert sk["sessions"] == 30                # the (from == to) row
    assert sk["to_segment"] == 7
    assert sk["to_segment_pct"] == 23.3        # 7/30
    assert sk["to_preorder"] == 5
    assert sk["to_preorder_pct"] == 16.7       # 5/30 — same denominator
    assert sk["leads"] == 2                    # internal address excluded
    assert sk["orders"] == 1

    # Quiz pages with no flow rows report zeros, not a crash or a missing row.
    assert rows["routine-break"]["sessions"] == 0
    assert rows["routine-break"]["to_segment_pct"] is None


def test_quiz_funnel_degrades_on_missing_tables(tmp_path: Path):
    from src.dashboard.db import get_quiz_funnel_table

    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()
    assert get_quiz_funnel_table(db, "2026-07-22", "2026-07-28") == []


# --- order journeys + ad traceback (migration 019) --------------------------
def test_looks_like_meta_id_separates_ad_ids_from_lp_slugs():
    """utm_content is overloaded: a numeric id means one Meta ad, anything else is
    a landing-page slug. Getting this wrong would invent ad attributions."""
    from src.dashboard.db import _looks_like_meta_id

    assert _looks_like_meta_id("120247134827400025") is True
    assert _looks_like_meta_id("home") is False
    assert _looks_like_meta_id("screen-anxious") is False
    assert _looks_like_meta_id("") is False
    # Too short to be a Meta id — must not be mistaken for one.
    assert _looks_like_meta_id("1") is False


def test_order_journeys_resolve_ad_and_lp_hint(tmp_path: Path):
    from src.dashboard.db import get_order_journeys

    db = tmp_path / "orders.db"
    build_migrated_db(db)
    con = sqlite3.connect(str(db))
    con.executescript("""
        INSERT INTO ad_creatives (ad_id, ad_name) VALUES
            ('120247134827400025','Nowa | HOME-04 | broad | single_image');
        INSERT INTO shopify_order_journey
            (order_id, order_name, created_at, order_date, financial_status,
             total_price, moments_count,
             first_utm_content, first_utm_term, last_utm_content, last_utm_term)
        VALUES
            -- traced to one ad via a numeric utm_content
            ('1','#1028','2026-07-24T10:00:00Z','2026-07-24','paid',116.0,1,
             '120247134827400025','120247134808750025',
             '120247134827400025','120247134808750025'),
            -- ad-set only: term is numeric, content is a page slug
            ('2','#1023','2026-07-18T10:00:00Z','2026-07-18','paid',116.0,1,
             'home','120247134808750025','home','120247134808750025'),
            -- two-touch journey, no ad ids at all
            ('3','#1026','2026-07-23T10:00:00Z','2026-07-23','paid',116.0,2,
             'screen-anxious','','home','');
    """)
    con.commit()
    con.close()

    rows = {r["order_name"]: r for r in get_order_journeys(db)}

    traced = rows["#1028"]
    assert traced["ad_id"] == "120247134827400025"
    assert traced["ad_name"] == "Nowa | HOME-04 | broad | single_image"
    assert traced["traced"] == "ad"
    assert traced["lp_hint"] == ""          # content was an id, not a slug

    adset_only = rows["#1023"]
    assert adset_only["ad_id"] == ""
    assert adset_only["adset_id"] == "120247134808750025"
    assert adset_only["traced"] == "adset"
    assert adset_only["lp_hint"] == "home"  # slug still surfaces

    two_touch = rows["#1026"]
    assert two_touch["traced"] == ""
    assert two_touch["moments_count"] == 2
    # The introduction is only visible because first and last differ.
    assert two_touch["first_utm_content"] == "screen-anxious"
    assert two_touch["last_utm_content"] == "home"


def test_order_journeys_degrade_on_missing_table(tmp_path: Path):
    from src.dashboard.db import get_order_journeys

    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()
    assert get_order_journeys(db) == []


@pytest.mark.asyncio
async def test_upsert_order_journeys_is_idempotent(db_client):
    """A journey can gain a moment between pulls; the fresher read must win."""
    row = {
        "order_id": "1", "order_name": "#1026", "created_at": "2026-07-23T10:00:00Z",
        "order_date": "2026-07-23", "financial_status": "paid", "total_price": 116.0,
        "moments_count": 1,
        **{f"first_{k}": "" for k in ("landing_page", "source", "source_type",
                                      "utm_source", "utm_medium", "utm_campaign",
                                      "utm_content", "utm_term")},
        **{f"last_{k}": "" for k in ("landing_page", "source", "source_type",
                                     "utm_source", "utm_medium", "utm_campaign",
                                     "utm_content", "utm_term")},
    }
    await db_client.upsert_order_journeys([row])
    await db_client.upsert_order_journeys([{**row, "moments_count": 2}])

    got = await db_client.fetch_all(
        "SELECT COUNT(*) AS n, MAX(moments_count) AS m FROM shopify_order_journey"
    )
    assert got[0]["n"] == 1
    assert got[0]["m"] == 2


def test_order_number_floor_excludes_pre_launch_test_orders(tmp_path: Path):
    """Test orders are identified by order NUMBER, which is their real identity —
    a date cutoff is only a proxy and would miss a test order placed after launch."""
    from src.dashboard.db import get_order_journeys

    db = tmp_path / "orders.db"
    build_migrated_db(db)
    con = sqlite3.connect(str(db))
    con.executescript("""
        INSERT INTO shopify_order_journey
            (order_id, order_name, created_at, order_date, financial_status, total_price)
        VALUES ('1','#1019','2026-07-13T10:00:00Z','2026-07-13','paid',221.0),
               ('2','#1020','2026-07-16T10:00:00Z','2026-07-16','paid',116.0),
               ('3','#1030','2026-07-29T10:00:00Z','2026-07-29','paid',116.0);
    """)
    con.commit()
    con.close()

    assert len(get_order_journeys(db)) == 3
    kept = get_order_journeys(db, min_order_number=1020)
    assert [r["order_name"] for r in kept] == ["#1030", "#1020"]
    assert kept[0]["order_number"] == 1030


def test_creative_code_traces_to_a_creative_not_an_ad_when_shared(tmp_path: Path):
    """utm_content=HOME-04 is a creative code, and several ad-sets reuse one code.
    Counting that as "traced to one ad" would overstate attribution."""
    from src.dashboard.db import get_order_journeys

    db = tmp_path / "orders.db"
    build_migrated_db(db)
    con = sqlite3.connect(str(db))
    con.executescript("""
        INSERT INTO ad_creatives (ad_id, ad_name) VALUES
            ('a1','Nowa | HOME-04 | broad | single_image | 20260715'),
            ('a2','Nowa | HOME-04 | lookalike | carousel | 20260715'),
            ('a3','Nowa | ROUTINE-09 | broad | single_image | 20260715');
        INSERT INTO shopify_order_journey
            (order_id, order_name, created_at, order_date, financial_status,
             total_price, last_utm_content)
        VALUES ('1','#1040','2026-07-29T10:00:00Z','2026-07-29','paid',116.0,'HOME-04'),
               ('2','#1041','2026-07-29T11:00:00Z','2026-07-29','paid',116.0,'ROUTINE-09');
    """)
    con.commit()
    con.close()

    rows = {r["order_name"]: r for r in get_order_journeys(db)}

    shared = rows["#1040"]
    assert shared["creative_code"] == "HOME-04"
    assert shared["traced"] == "creative"        # 2 ads share it -> not one ad
    assert len(shared["creative_ad_names"]) == 2
    assert shared["ad_id"] == ""

    unique = rows["#1041"]
    assert unique["creative_code"] == "ROUTINE-09"
    assert unique["traced"] == "ad"              # only one ad carries it
    assert "ROUTINE-09" in unique["ad_name"]
