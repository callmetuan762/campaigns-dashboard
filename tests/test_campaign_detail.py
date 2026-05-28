"""Phase 7 (DASH-07): drill-down page + get_campaign_daily SQL tests."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.dashboard.db import get_campaign_daily


# --- Fixture ---------------------------------------------------------------

@pytest.fixture
def db_with_data(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    con = sqlite3.connect(str(db))
    con.executescript(
        '''
        CREATE TABLE campaigns (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
        CREATE TABLE ad_metrics (
            campaign_id INTEGER, date TEXT, ad_set_id TEXT DEFAULT '', ad_id TEXT DEFAULT '',
            spend REAL, roas REAL, impressions INTEGER, clicks INTEGER, ctr REAL,
            meta_purchases_7dclick INTEGER, meta_form_submit_deposit INTEGER, fetched_at TEXT
        );
        CREATE TABLE ga4_metrics (
            date TEXT, campaign_utm TEXT, sessions INTEGER, users INTEGER,
            bounce_rate REAL, ga4_purchases_lastclick INTEGER, fetched_at TEXT
        );

        INSERT INTO campaigns(id, name) VALUES (1, 'Demo'), (2, 'Other');

        -- Campaign-level rows for Demo (3 dates)
        INSERT INTO ad_metrics VALUES
            (1, '2025-05-01', '', '', 100.0, 2.0, 1000, 50, 5.0, 3, 2, '2025-05-02T00:00:00'),
            (1, '2025-05-02', '', '', 200.0, 1.5, 2000, 80, 4.0, 5, 3, '2025-05-03T00:00:00'),
            (1, '2025-05-03', '', '', 150.0, 2.5, 1500, 60, 4.0, 4, 2, '2025-05-04T00:00:00');

        -- Ad-set-level row (must be EXCLUDED by ad_set_id = '' filter)
        INSERT INTO ad_metrics VALUES
            (1, '2025-05-01', 'AS1', '', 999.0, 0.1, 100, 1, 1.0, 0, 0, '2025-05-02T00:00:00');

        -- Other campaign row (must NOT appear in Demo results)
        INSERT INTO ad_metrics VALUES
            (2, '2025-05-01', '', '', 500.0, 5.0, 9999, 100, 1.0, 0, 0, '2025-05-02T00:00:00');

        -- GA4 rows matched by campaign_utm + date
        INSERT INTO ga4_metrics VALUES
            ('2025-05-01', 'Demo', 200, 100, 0.3, 1, '2025-05-02T00:00:00'),
            ('2025-05-02', 'Demo', 250, 120, 0.25, 2, '2025-05-03T00:00:00');
            -- 2025-05-03 has no GA4 row -> sessions should be 0
        '''
    )
    con.commit()
    con.close()
    return db


# --- get_campaign_daily ----------------------------------------------------

class TestGetCampaignDaily:
    def test_empty_db_returns_empty_list(self, tmp_path):
        db = tmp_path / "empty.db"
        con = sqlite3.connect(str(db))
        con.executescript(
            '''
            CREATE TABLE campaigns (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE ad_metrics (
                campaign_id INTEGER, date TEXT, ad_set_id TEXT, ad_id TEXT,
                spend REAL, roas REAL, meta_purchases_7dclick INTEGER,
                meta_form_submit_deposit INTEGER
            );
            CREATE TABLE ga4_metrics (
                date TEXT, campaign_utm TEXT, sessions INTEGER, ga4_purchases_lastclick INTEGER
            );
            '''
        )
        con.commit()
        con.close()
        rows = get_campaign_daily(db, "Demo", "2025-05-01", "2025-05-31")
        assert rows == []

    def test_returns_one_row_per_date_ordered_ascending(self, db_with_data):
        rows = get_campaign_daily(db_with_data, "Demo", "2025-05-01", "2025-05-31")
        assert len(rows) == 3
        dates = [r["date"] for r in rows]
        assert dates == sorted(dates)

    def test_row_shape(self, db_with_data):
        rows = get_campaign_daily(db_with_data, "Demo", "2025-05-01", "2025-05-31")
        keys = set(rows[0].keys())
        assert {"date", "spend", "deposits", "sessions", "roas",
                "meta_purchases", "ga4_purchases"} <= keys

    def test_excludes_ad_set_level_rows(self, db_with_data):
        rows = get_campaign_daily(db_with_data, "Demo", "2025-05-01", "2025-05-01")
        assert len(rows) == 1
        # The ad-set row had spend=999; if it leaked in, spend would be 999+100=1099
        assert rows[0]["spend"] == pytest.approx(100.0)

    def test_ga4_matched_by_utm_and_date(self, db_with_data):
        rows = get_campaign_daily(db_with_data, "Demo", "2025-05-01", "2025-05-03")
        by_date = {r["date"]: r for r in rows}
        assert by_date["2025-05-01"]["sessions"] == 200
        assert by_date["2025-05-02"]["sessions"] == 250
        # No GA4 row for 2025-05-03 -> COALESCE gives 0 (not NULL)
        assert by_date["2025-05-03"]["sessions"] == 0

    def test_date_range_inclusive(self, db_with_data):
        rows = get_campaign_daily(db_with_data, "Demo", "2025-05-02", "2025-05-02")
        assert len(rows) == 1
        assert rows[0]["date"] == "2025-05-02"

    def test_no_cross_campaign_leakage(self, db_with_data):
        rows = get_campaign_daily(db_with_data, "Other", "2025-05-01", "2025-05-31")
        assert len(rows) == 1
        assert rows[0]["spend"] == pytest.approx(500.0)

    def test_unknown_campaign_returns_empty(self, db_with_data):
        rows = get_campaign_daily(db_with_data, "Nonexistent", "2025-05-01", "2025-05-31")
        assert rows == []

    def test_deposits_and_meta_purchases_are_separate_keys(self, db_with_data):
        rows = get_campaign_daily(db_with_data, "Demo", "2025-05-01", "2025-05-01")
        row = rows[0]
        # meta_form_submit_deposit -> deposits; meta_purchases_7dclick -> meta_purchases
        assert row["deposits"] == 2
        assert row["meta_purchases"] == 3

    def test_ga4_purchases_not_blended_with_meta(self, db_with_data):
        rows = get_campaign_daily(db_with_data, "Demo", "2025-05-01", "2025-05-01")
        row = rows[0]
        # ga4 row has ga4_purchases_lastclick=1; meta has meta_purchases_7dclick=3
        assert row["ga4_purchases"] == 1
        assert row["meta_purchases"] == 3
        # Must be separate keys, never summed
        assert "ga4_purchases" in row
        assert "meta_purchases" in row


# --- Page module importability --------------------------------------------

class TestPageModule:
    def test_campaign_detail_page_parses(self):
        """Module must be valid Python so Streamlit can load it."""
        import ast
        path = Path("src/dashboard/pages/1_Campaign_Detail.py")
        assert path.exists(), "1_Campaign_Detail.py not found"
        ast.parse(path.read_text(encoding="utf-8"))

    def test_ai_chat_page_parses(self):
        import ast
        path = Path("src/dashboard/pages/2_AI_Chat.py")
        assert path.exists(), "2_AI_Chat.py not found"
        ast.parse(path.read_text(encoding="utf-8"))

    def test_campaign_detail_uses_query_params(self):
        path = Path("src/dashboard/pages/1_Campaign_Detail.py")
        src = path.read_text(encoding="utf-8")
        assert "st.query_params" in src

    def test_campaign_detail_uses_get_campaign_daily(self):
        path = Path("src/dashboard/pages/1_Campaign_Detail.py")
        src = path.read_text(encoding="utf-8")
        assert "get_campaign_daily" in src

    def test_ai_chat_uses_run_chat_3agent(self):
        path = Path("src/dashboard/pages/2_AI_Chat.py")
        src = path.read_text(encoding="utf-8")
        assert "run_chat_3agent" in src

    def test_ai_chat_uses_independent_history_key(self):
        path = Path("src/dashboard/pages/2_AI_Chat.py")
        src = path.read_text(encoding="utf-8")
        assert "chat_page_history" in src

    def test_attribution_caption_present(self):
        """Phase 7 DASH-07: drill-down must carry the never-blend attribution note."""
        path = Path("src/dashboard/pages/1_Campaign_Detail.py")
        src = path.read_text(encoding="utf-8")
        assert "Never blend" in src or "7-day click" in src
