"""Unit tests for src/mmm/model.py (MMM-01).

Covers:
- adstock() recursive decay
- hill_saturation() formula
- MMMResult dataclass + to_dict
- fit_mmm() guard conditions + synthetic data success path
- optimal spend derivation (km * 4^(1/n))
- ROAS sanity cap
- maturity_label thresholds
"""
from __future__ import annotations

import numpy as np
import pytest


# --------------------------------------------------------------------------
# adstock()
# --------------------------------------------------------------------------

def test_adstock_recursive_decay() -> None:
    """adstock[t] = spend[t] + theta * adstock[t-1] (sequential, NOT cumsum)."""
    from src.mmm.model import adstock

    spend = np.array([100.0, 200.0, 300.0])
    out = adstock(spend, theta=0.5)
    # t=0: 100
    # t=1: 200 + 0.5*100 = 250
    # t=2: 300 + 0.5*250 = 425
    assert out[0] == pytest.approx(100.0)
    assert out[1] == pytest.approx(250.0)
    assert out[2] == pytest.approx(425.0)


def test_adstock_theta_zero_equals_spend() -> None:
    """theta=0 means no carryover — output == input."""
    from src.mmm.model import adstock

    spend = np.array([100.0])
    out = adstock(spend, theta=0.0)
    assert out[0] == pytest.approx(100.0)


def test_adstock_multi_step_theta_zero() -> None:
    """theta=0 across multiple days still == input."""
    from src.mmm.model import adstock

    spend = np.array([10.0, 20.0, 30.0])
    out = adstock(spend, theta=0.0)
    np.testing.assert_array_almost_equal(out, spend)


# --------------------------------------------------------------------------
# hill_saturation()
# --------------------------------------------------------------------------

def test_hill_saturation_at_half_saturation_is_one_half() -> None:
    """f(Km) = Km^n / (Km^n + Km^n) = 0.5 by definition."""
    from src.mmm.model import hill_saturation

    km = 100.0
    out = hill_saturation(np.array([km]), km=km, n=1.0)
    assert out[0] == pytest.approx(0.5)


def test_hill_saturation_with_n2() -> None:
    """f(x) = x^n / (km^n + x^n). At x=km, output = 0.5 regardless of n."""
    from src.mmm.model import hill_saturation

    km = 50.0
    out = hill_saturation(np.array([km, km * 2]), km=km, n=2.0)
    assert out[0] == pytest.approx(0.5)
    # at x=2*km, output = 4 / (1 + 4) = 0.8
    assert out[1] == pytest.approx(0.8)


# --------------------------------------------------------------------------
# fit_mmm() guard conditions
# --------------------------------------------------------------------------

def test_fit_mmm_returns_none_on_insufficient_data() -> None:
    """len(spend) < 7 → None."""
    from src.mmm.model import fit_mmm

    spend = np.array([100.0] * 5)
    deposits = np.array([5.0] * 5)
    out = fit_mmm(spend, deposits)
    assert out is None


def test_fit_mmm_returns_none_on_all_zero_deposits() -> None:
    """All-zero deposits → None."""
    from src.mmm.model import fit_mmm

    spend = np.array([100.0] * 10)
    deposits = np.array([0.0] * 10)
    out = fit_mmm(spend, deposits)
    assert out is None


def test_fit_mmm_returns_none_on_all_zero_spend() -> None:
    """All-zero spend → None."""
    from src.mmm.model import fit_mmm

    spend = np.array([0.0] * 10)
    deposits = np.array([5.0] * 10)
    out = fit_mmm(spend, deposits)
    assert out is None


def test_fit_mmm_returns_none_on_too_many_zero_deposit_days() -> None:
    """>70% zero deposit days → None."""
    from src.mmm.model import fit_mmm

    spend = np.array([100.0] * 10)
    # 8 of 10 days are zero (80%) — above 70% threshold
    deposits = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 5.0, 4.0])
    out = fit_mmm(spend, deposits)
    assert out is None


# --------------------------------------------------------------------------
# fit_mmm() synthetic happy path
# --------------------------------------------------------------------------

def _synthetic_data(n_days: int = 30, seed: int = 42) -> tuple[np.ndarray, np.ndarray]:
    """Generate spend in [100, 500] with deposits correlated via Hill saturation."""
    rng = np.random.default_rng(seed)
    spend = rng.uniform(100, 500, size=n_days)
    # True Hill: km=250, n=1.5; deposits = baseline + media * hill(spend)
    true_sat = (spend ** 1.5) / (250.0 ** 1.5 + spend ** 1.5)
    deposits = 2.0 + 8.0 * true_sat + rng.normal(0, 0.3, size=n_days)
    deposits = np.maximum(deposits, 0.0)  # no negative deposits
    return spend, deposits


def test_fit_mmm_synthetic_returns_valid_result() -> None:
    """Valid 30-day synthetic data → MMMResult with sane fields."""
    from src.mmm.model import MMMResult, fit_mmm

    spend, deposits = _synthetic_data(n_days=30)
    result = fit_mmm(spend, deposits, run_date="2026-05-24", weeks_of_data=4)
    assert result is not None
    assert isinstance(result, MMMResult)
    assert 0.0 <= result.media_pct <= 100.0
    assert 0.0 <= result.baseline_pct <= 100.0
    assert result.optimal_daily_spend > 0
    assert result.run_date == "2026-05-24"
    assert result.weeks_of_data == 4


def test_fit_mmm_media_plus_baseline_equals_100() -> None:
    """media_pct + baseline_pct ≈ 100 (within 0.2 tolerance for rounding)."""
    from src.mmm.model import fit_mmm

    spend, deposits = _synthetic_data(n_days=30, seed=7)
    result = fit_mmm(spend, deposits)
    assert result is not None
    assert abs(result.media_pct + result.baseline_pct - 100.0) < 0.2


def test_fit_mmm_to_dict_has_all_fields() -> None:
    """MMMResult.to_dict() includes all 10 fields."""
    from src.mmm.model import fit_mmm

    spend, deposits = _synthetic_data(n_days=30)
    result = fit_mmm(spend, deposits, run_date="2026-05-24", weeks_of_data=4)
    assert result is not None

    d = result.to_dict()
    expected_keys = {
        "run_date",
        "weeks_of_data",
        "media_pct",
        "baseline_pct",
        "incremental_roas_per_1k",
        "optimal_daily_spend",
        "theta",
        "km",
        "n",
        "maturity_label",
    }
    assert expected_keys == set(d.keys())


# --------------------------------------------------------------------------
# optimal_daily_spend = km * 4^(1/n)
# --------------------------------------------------------------------------

def test_fit_mmm_optimal_spend_formula() -> None:
    """optimal_daily_spend matches km * 4^(1/n) derivation."""
    from src.mmm.model import fit_mmm

    spend, deposits = _synthetic_data(n_days=30)
    result = fit_mmm(spend, deposits)
    assert result is not None
    expected = result.km * (4.0 ** (1.0 / result.n))
    assert result.optimal_daily_spend == pytest.approx(expected, rel=1e-2)


# --------------------------------------------------------------------------
# Incremental ROAS behaviour
# --------------------------------------------------------------------------

def test_fit_mmm_roas_per_1k_when_deposit_value_zero() -> None:
    """When deposit_value_usd=0.0 → incremental_roas_per_1k holds deposits-per-$1000."""
    from src.mmm.model import fit_mmm

    spend, deposits = _synthetic_data(n_days=30)
    result = fit_mmm(spend, deposits, deposit_value_usd=0.0)
    assert result is not None
    # When deposit_value_usd=0 the field holds deposits-per-$1000 and is positive
    assert result.incremental_roas_per_1k is not None
    assert result.incremental_roas_per_1k > 0


def test_fit_mmm_roas_with_deposit_value() -> None:
    """When deposit_value_usd > 0 → ROAS positive, below 100x cap for realistic value."""
    from src.mmm.model import fit_mmm

    spend, deposits = _synthetic_data(n_days=30)
    # 100 USD per deposit — realistic, won't trip 100x cap
    result = fit_mmm(spend, deposits, deposit_value_usd=100.0)
    assert result is not None
    assert result.incremental_roas_per_1k is not None
    assert result.incremental_roas_per_1k > 0


def test_fit_mmm_roas_sanity_cap_suppressed() -> None:
    """ROAS > 100x triggers suppression — incremental_roas_per_1k = None."""
    from src.mmm.model import fit_mmm

    # Inflate deposit_value_usd to force ROAS > 100 with the synthetic data
    spend, deposits = _synthetic_data(n_days=30)
    result = fit_mmm(spend, deposits, deposit_value_usd=1_000_000.0)
    assert result is not None
    # With $1M per deposit, ROAS will exceed 100 and be suppressed
    assert result.incremental_roas_per_1k is None


# --------------------------------------------------------------------------
# maturity_label thresholds
# --------------------------------------------------------------------------

def test_fit_mmm_maturity_directional_only_below_8_weeks() -> None:
    """weeks_of_data < 8 → 'directional_only'."""
    from src.mmm.model import fit_mmm

    spend, deposits = _synthetic_data(n_days=30)
    result = fit_mmm(spend, deposits, weeks_of_data=4)
    assert result is not None
    assert result.maturity_label == "directional_only"


def test_fit_mmm_maturity_early_8_to_11_weeks() -> None:
    """8 ≤ weeks_of_data < 12 → 'early'."""
    from src.mmm.model import fit_mmm

    spend, deposits = _synthetic_data(n_days=30)
    result = fit_mmm(spend, deposits, weeks_of_data=10)
    assert result is not None
    assert result.maturity_label == "early"


def test_fit_mmm_maturity_reliable_at_12_weeks() -> None:
    """weeks_of_data ≥ 12 → 'reliable'."""
    from src.mmm.model import fit_mmm

    spend, deposits = _synthetic_data(n_days=30)
    result = fit_mmm(spend, deposits, weeks_of_data=12)
    assert result is not None
    assert result.maturity_label == "reliable"
