"""Phase 7 (DASH-06): TIER classification unit tests."""
from __future__ import annotations

import os

import pandas as pd
import pytest

os.environ.setdefault("DASHBOARD_PASSWORD", "")

from src.dashboard.Overview import _tier_tag, _format_campaign_df


# --- _tier_tag pure function -----------------------------------------------

class TestTierTag:
    def test_hidden_when_target_zero(self):
        assert _tier_tag(50.0, 5, 0.0) == ""
        assert _tier_tag(None, 0, 0.0) == ""

    def test_paused_when_deposits_zero(self):
        assert _tier_tag(10.0, 0, 25.0) == "PAUSED"
        assert _tier_tag(0.5, 0, 25.0) == "PAUSED"

    def test_paused_when_cpd_none(self):
        assert _tier_tag(None, 5, 25.0) == "PAUSED"

    def test_scale_below_target(self):
        assert _tier_tag(20.0, 5, 25.0) == "★ SCALE"

    def test_scale_at_target_boundary(self):
        # cpd == target -> SCALE (inclusive)
        assert _tier_tag(25.0, 5, 25.0) == "★ SCALE"

    def test_maintain_between_target_and_1_3x(self):
        assert _tier_tag(30.0, 5, 25.0) == "MAINTAIN"

    def test_maintain_at_1_3x_boundary(self):
        # cpd == target * 1.3 -> MAINTAIN (inclusive)
        assert _tier_tag(32.5, 5, 25.0) == "MAINTAIN"

    def test_reduce_above_1_3x(self):
        assert _tier_tag(40.0, 5, 25.0) == "REDUCE"

    def test_paused_precedence_over_cpd_check(self):
        # Even with a great CPD, zero deposits = PAUSED
        assert _tier_tag(1.0, 0, 25.0) == "PAUSED"

    def test_negative_target_treated_as_disabled(self):
        # cpd_target <= 0.0 -> hidden (D-04)
        assert _tier_tag(5.0, 10, -1.0) == ""


# --- _format_campaign_df conditional TIER column ----------------------------
#
# Row dict keys and columns follow the FSD -> Initiate Checkout re-point
# (2026-07-22): "deposits"/"cpd" -> "begin_checkout"/"cost_per_bc", and a
# "Goal" column (Meta campaign objective) was added ahead of the metric
# columns -- see src/dashboard/Overview.py::_format_campaign_df.

class TestFormatCampaignDf:
    BASE_COLS = ["Campaign", "Goal", "Spend", "ROAS", "Impressions",
                 "Initiate Checkout", "CPR (Initiate Checkout)", "GA4 Sessions"]

    def test_empty_rows_no_target_phase6_shape(self):
        df = _format_campaign_df([], cpd_target=0.0)
        assert list(df.columns) == self.BASE_COLS
        assert len(df) == 0

    def test_empty_rows_with_target_adds_tier_column(self):
        df = _format_campaign_df([], cpd_target=25.0)
        assert list(df.columns) == self.BASE_COLS + ["TIER"]
        assert len(df) == 0

    def _row(self, **kw):
        base = dict(
            campaign_name="x", spend=100.0, weighted_roas=2.0,
            impressions=1000, begin_checkout=5, cost_per_bc=20.0, ga4_sessions=50,
        )
        base.update(kw)
        return base

    def test_tier_populated_for_scale(self):
        df = _format_campaign_df([self._row(cost_per_bc=20.0, begin_checkout=5)], cpd_target=25.0)
        assert df.iloc[0]["TIER"] == "★ SCALE"

    def test_tier_populated_for_paused_zero_deposits(self):
        df = _format_campaign_df([self._row(cost_per_bc=20.0, begin_checkout=0)], cpd_target=25.0)
        assert df.iloc[0]["TIER"] == "PAUSED"

    def test_tier_populated_for_maintain(self):
        df = _format_campaign_df([self._row(cost_per_bc=30.0, begin_checkout=5)], cpd_target=25.0)
        assert df.iloc[0]["TIER"] == "MAINTAIN"

    def test_tier_populated_for_reduce(self):
        df = _format_campaign_df([self._row(cost_per_bc=50.0, begin_checkout=5)], cpd_target=25.0)
        assert df.iloc[0]["TIER"] == "REDUCE"

    def test_no_tier_column_when_target_zero(self):
        df = _format_campaign_df([self._row()], cpd_target=0.0)
        assert "TIER" not in df.columns

    def test_multiple_rows_all_classified(self):
        rows = [
            self._row(campaign_name="A", cost_per_bc=10.0, begin_checkout=5),   # SCALE
            self._row(campaign_name="B", cost_per_bc=30.0, begin_checkout=5),   # MAINTAIN
            self._row(campaign_name="C", cost_per_bc=60.0, begin_checkout=5),   # REDUCE
            self._row(campaign_name="D", cost_per_bc=10.0, begin_checkout=0),   # PAUSED
        ]
        df = _format_campaign_df(rows, cpd_target=25.0)
        assert list(df["TIER"]) == ["★ SCALE", "MAINTAIN", "REDUCE", "PAUSED"]
