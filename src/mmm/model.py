"""Lightweight Marketing Mix Model — geometric adstock + Hill saturation + OLS.

Pure function module. No global state, no I/O. Inputs are numpy arrays;
output is a MMMResult dataclass (or None on guard failures / convergence failures).

Implements D-01..D-05 from .planning/phases/08-mmm-attribution-intelligence/08-CONTEXT.md:
- D-01: lightweight Python MMM (statsmodels OLS + scipy curve_fit, no Bayesian, no R Robyn)
- D-02: geometric adstock decay (single theta parameter)
- D-03: Hill saturation (two parameters Km + n)
- D-04: OLS decomposition deposits ~ const + media
- D-05: optimal_daily_spend = km * 4^(1/n) (80% saturation)

Algorithm steps (RESEARCH Pattern 4 two-pass fit):
1. Initial Hill fit with raw spend (theta=0) → starting Km, n
2. Theta grid search over np.linspace(0.0, 0.9, 10) → best theta minimizing OLS RSS
3. Re-fit Hill on adstocked spend with best theta → final Km, n
4. OLS decomposition on Hill-saturated adstocked spend → media_pct, baseline_pct
5. Derive optimal_daily_spend and incremental_roas_per_1k
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
import statsmodels.api as sm
import structlog
from scipy.optimize import curve_fit

logger = structlog.get_logger(__name__)


def adstock(spend: np.ndarray, theta: float) -> np.ndarray:
    """Geometric adstock transform.

    adstock[0] = spend[0]
    adstock[t] = spend[t] + theta * adstock[t-1]

    Sequential loop is intentional — cumsum/cumprod approximations give wrong
    answers for non-unit theta (RESEARCH anti-pattern).
    """
    result = np.empty_like(spend, dtype=float)
    result[0] = spend[0]
    for i in range(1, len(spend)):
        result[i] = spend[i] + theta * result[i - 1]
    return result


def hill_saturation(x: np.ndarray, km: float, n: float) -> np.ndarray:
    """Hill saturation function: f(x) = x^n / (Km^n + x^n).

    Properties:
    - f(0) = 0
    - f(Km) = 0.5 (half-saturation by definition)
    - f(x) → 1 as x → ∞
    """
    return (x ** n) / (km ** n + x ** n)


@dataclass
class MMMResult:
    """One MMM fit result. Maps 1:1 to mmm_results SQLite columns."""

    run_date: str  # ISO YYYY-MM-DD
    weeks_of_data: int
    media_pct: float  # already multiplied by 100, e.g. 42.3
    baseline_pct: float  # already multiplied by 100, e.g. 57.7
    incremental_roas_per_1k: Optional[float]
    optimal_daily_spend: float
    theta: float
    km: float
    n: float
    maturity_label: str  # 'directional_only' | 'early' | 'reliable'

    def to_dict(self) -> dict:
        return {
            "run_date": self.run_date,
            "weeks_of_data": self.weeks_of_data,
            "media_pct": self.media_pct,
            "baseline_pct": self.baseline_pct,
            "incremental_roas_per_1k": self.incremental_roas_per_1k,
            "optimal_daily_spend": self.optimal_daily_spend,
            "theta": self.theta,
            "km": self.km,
            "n": self.n,
            "maturity_label": self.maturity_label,
        }


def _hill_func(x: np.ndarray, km: float, n: float) -> np.ndarray:
    """curve_fit-compatible Hill (module-level so curve_fit can pickle if needed)."""
    return (x ** n) / (km ** n + x ** n)


def fit_mmm(
    spend: np.ndarray,
    deposits: np.ndarray,
    deposit_value_usd: float = 0.0,
    run_date: str = "",
    weeks_of_data: int = 0,
) -> MMMResult | None:
    """Fit geometric adstock + Hill saturation + OLS decomposition.

    Returns MMMResult on success, None on:
    - len(spend) < 7
    - all-zero deposits or all-zero spend
    - > 70% zero-deposit days (sparse data, model unreliable)
    - curve_fit RuntimeError/ValueError on Hill init or refit
    - Hill n at bound (n <= 0.51 or n >= 2.99 — fit hit boundary, not converged)

    Pure function — touches no module globals.
    """
    # ---- Guard 1: insufficient observations -------------------------------
    if len(spend) < 7 or np.all(deposits == 0) or np.all(spend == 0):
        logger.warning("mmm_insufficient_data", n=len(spend))
        return None

    # ---- Guard 2: too many zero-deposit days ------------------------------
    zero_pct = float(np.sum(deposits == 0)) / float(len(deposits))
    if zero_pct > 0.7:
        logger.warning("mmm_too_many_zero_deposit_days", zero_pct=zero_pct)
        return None

    # Normalize deposits for curve_fit numerical stability (Hill output ∈ [0,1]).
    deposits_max = float(np.max(deposits))
    if deposits_max <= 0:
        logger.warning("mmm_insufficient_data", reason="deposits_max_zero")
        return None
    deposits_norm = deposits / deposits_max

    positive_spend = spend[spend > 0]
    if len(positive_spend) == 0:
        logger.warning("mmm_insufficient_data", reason="no_positive_spend")
        return None
    mean_spend = float(np.mean(positive_spend))

    bounds_lo = [1e-6, 0.5]
    bounds_hi = [mean_spend * 10.0, 3.0]

    # ---- Pass 1: Initial Hill fit with raw spend (theta=0) ----------------
    try:
        popt_init, _ = curve_fit(
            _hill_func,
            spend,
            deposits_norm,
            p0=[mean_spend, 1.0],
            bounds=(bounds_lo, bounds_hi),
            maxfev=5000,
        )
        km_init, n_init = float(popt_init[0]), float(popt_init[1])
    except (RuntimeError, ValueError) as exc:
        logger.warning("mmm_hill_init_failed", error=str(exc))
        return None

    # ---- Pass 2: Theta grid search ----------------------------------------
    best_theta = 0.0
    best_rss = float("inf")
    for theta in np.linspace(0.0, 0.9, 10):
        ads = adstock(spend, float(theta))
        sat = hill_saturation(ads, km_init, n_init)
        X = sm.add_constant(pd.Series(sat, name="media"))
        try:
            res = sm.OLS(deposits, X).fit()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("mmm_ols_grid_failed", theta=float(theta), error=str(exc))
            continue
        rss = float(np.sum(res.resid ** 2))
        if rss < best_rss:
            best_rss = rss
            best_theta = float(theta)

    # ---- Pass 3: Re-fit Hill on adstocked spend with best theta -----------
    ads_best = adstock(spend, best_theta)
    try:
        popt, _ = curve_fit(
            _hill_func,
            ads_best,
            deposits_norm,
            p0=[km_init, n_init],
            bounds=(bounds_lo, bounds_hi),
            maxfev=5000,
        )
        km, n = float(popt[0]), float(popt[1])
    except (RuntimeError, ValueError) as exc:
        logger.warning("mmm_hill_refit_failed", error=str(exc))
        return None

    # ---- Boundary check on n ----------------------------------------------
    if n <= 0.51 or n >= 2.99:
        logger.warning("mmm_n_at_boundary", n=n)
        return None

    # ---- OLS decomposition on final saturated adstocked series ------------
    sat_best = hill_saturation(ads_best, km, n)
    X = sm.add_constant(pd.Series(sat_best, name="media"))
    ols_res = sm.OLS(deposits, X).fit()

    baseline_coeff = float(ols_res.params["const"])
    media_coeff = float(ols_res.params["media"])
    if baseline_coeff < 0:
        logger.warning("mmm_negative_intercept", baseline=baseline_coeff)

    fitted = ols_res.predict(X)
    total_fitted = float(np.sum(fitted))
    media_contribution = media_coeff * sat_best
    media_sum = float(np.sum(media_contribution))

    if total_fitted > 0:
        media_pct = max(0.0, min(1.0, media_sum / total_fitted))
    else:
        media_pct = 0.0

    # ---- Optimal daily spend (80% saturation) -----------------------------
    opt_spend = float(km * (4.0 ** (1.0 / n)))

    # ---- Incremental ROAS -------------------------------------------------
    total_spend = float(np.sum(spend))
    incremental_roas: float | None = None
    if total_spend > 0:
        if deposit_value_usd > 0:
            # True dollar ROAS
            incremental_value = media_sum * deposit_value_usd
            roas = incremental_value / total_spend
            if roas > 100:
                logger.warning("mmm_roas_sanity_cap", roas=roas)
                incremental_roas = None
            else:
                incremental_roas = roas
        else:
            # "Deposits per $1000 spend" when no dollar value provided
            incremental_roas = (media_sum / total_spend) * 1000.0

    # ---- Maturity label ---------------------------------------------------
    if weeks_of_data < 8:
        maturity = "directional_only"
    elif weeks_of_data < 12:
        maturity = "early"
    else:
        maturity = "reliable"

    return MMMResult(
        run_date=run_date,
        weeks_of_data=weeks_of_data,
        media_pct=round(media_pct * 100.0, 1),
        baseline_pct=round((1.0 - media_pct) * 100.0, 1),
        incremental_roas_per_1k=round(incremental_roas, 2) if incremental_roas is not None else None,
        optimal_daily_spend=round(opt_spend, 2),
        theta=round(best_theta, 3),
        km=round(km, 4),
        n=round(n, 4),
        maturity_label=maturity,
    )
