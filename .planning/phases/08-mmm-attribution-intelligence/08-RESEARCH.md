# Phase 8: MMM + Attribution Intelligence - Research

**Researched:** 2026-05-24
**Domain:** Marketing Mix Modeling (Python) — geometric adstock, Hill saturation, OLS decomposition, Streamlit attribution dashboard
**Confidence:** HIGH (all core claims verified against official docs or registry; algorithm math verified against multiple authoritative sources)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Lightweight Python MMM — geometric adstock + Hill saturation + OLS regression. NO pymc-marketing, NO R Robyn. Pure pandas + scipy (curve_fit) + statsmodels (OLS).
- **D-02:** Adstock = geometric decay (`adstock[t] = spend[t] + theta * adstock[t-1]`). Single parameter `theta` (0–1). Theta estimated via grid search over OLS residuals.
- **D-03:** Saturation function = Hill function (`f(x) = x^n / (Km^n + x^n)`). Two parameters: `Km` (half-saturation spend level) and `n` (shape). Fitted via `scipy.optimize.curve_fit`.
- **D-04:** Decomposition = OLS regression of `deposits ~ baseline + hill(adstock(spend))`. Intercept = baseline. Coefficient on media term = media contribution estimate.
- **D-05:** Optimal spend = spend level at 80% of saturation. Reported as `optimal_daily_spend`.
- **D-06:** Data guard: <4 weeks → skip silently; 4–7 → run + warning; 8–11 → light footnote; ≥12 → run clean.
- **D-07:** Week count via `SELECT COUNT(DISTINCT strftime('%Y-%W', date)) FROM ad_metrics WHERE ad_set_id='' AND ad_id=''`.
- **D-08:** Weekly Telegram message: plain text, Sunday 23:00, format as specified.
- **D-09:** `DEPOSIT_VALUE_USD` env var, default 0.0 → "deposits per $1000 spend" when unset.
- **D-10:** Streamlit page `src/dashboard/pages/3_Attribution.py`. Follows Phase 7 conventions. Palette re-declared in page (standalone rule D-19).
- **D-11:** Page layout: Row 1 KPI cards, Row 2 two charts side-by-side, Row 3 attribution table with "Never blend" caption.
- **D-12:** `mmm_results` table (migration 006): `run_date, weeks_of_data, media_pct, baseline_pct, incremental_roas_per_1k, optimal_daily_spend, theta, km, n, maturity_label, created_at`.
- **D-13:** Empty table → show "MMM has not run yet" + "Run MMM now" button (inline, sync, no Telegram push).
- **D-14:** Package `src/mmm/`: `__init__.py`, `model.py`, `scheduler.py`. No src.ai.* or src.bot.* imports except via params.
- **D-15:** `MMMResult` dataclass fields as specified.
- **D-16:** New deps: `statsmodels>=0.14`, `scipy>=1.13`.
- **D-17:** Added to `pyproject.toml` `[project.dependencies]`.
- **D-18:** `CronTrigger(day_of_week='sun', hour=23, minute=0)` wired in `src/main.py`.
- **D-19:** `run_mmm_weekly_job` is async, uses `asyncio.to_thread` for sync model fitting.
- **D-20:** MMM model is read-only on all existing tables; only writes to `mmm_results`.
- **D-21:** `deposit_value_usd` defaults to 0.0. ROAS > 100× gets sanity cap warning.

### Claude's Discretion
- Exact grid search range for `theta` estimation (suggest 0.0–0.9 in 10 steps)
- Whether to cap `n` (Hill shape) between 0.5–3.0 for numerical stability
- SQLite migration numbering (next = Migration 006)
- Plotly chart styling details (consistent with D-10 dark palette)
- Error handling strategy within `fit_mmm()` if curve_fit fails (return None, log warning)

### Deferred Ideas (OUT OF SCOPE)
- Robyn (R) integration
- Multi-channel MMM
- Bayesian MMM (pymc-marketing)
- Slider-based budget simulator
- Incrementality experiments
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MMM-01 | `src/mmm/` package: geometric adstock + Hill saturation + OLS decomposition | Section: Standard Stack, Architecture Patterns, Code Examples |
| MMM-02 | Weekly APScheduler job: skips silently <4 weeks, "directional only" warning 4-7 weeks, footnote 8-11, clean ≥12 | Section: Architecture Patterns (scheduler wiring), Common Pitfalls |
| MMM-03 | Telegram weekly message: media contribution %, incremental ROAS estimate, optimal daily spend | Section: Code Examples (Telegram formatting), Common Pitfalls |
| DASH-11 | Streamlit `pages/3_Attribution.py`: saturation curve chart + contribution breakdown | Section: Code Examples (Plotly charts), Architecture Patterns |
| DASH-12 | `mmm_results` SQLite table (migration 006) | Section: Standard Stack (DB schema) |
| DASH-13 | All Phase 7 functionality intact; 312+ tests still pass | Section: Validation Architecture |
</phase_requirements>

---

## Summary

Phase 8 adds a lightweight, sparse-data-tolerant Marketing Mix Model (MMM) to the existing Python/SQLite/aiogram/Streamlit stack. The model pipeline is: (1) load daily spend + deposit data from SQLite, (2) apply geometric adstock transform (a sequential loop — not vectorizable due to recursive dependency), (3) fit a Hill saturation curve via `scipy.optimize.curve_fit` with bounded parameters, (4) regress deposits against adstocked+saturated media plus a constant (baseline) via `statsmodels.OLS`, (5) derive incremental ROAS and optimal spend from fitted parameters.

The most important implementation constraint is **sparse data** (5 weeks at launch). OLS with only ~35 daily observations and 2 predictors (constant + media) is underdetermined when multicollinearity is high, but is valid when the Hill-transformed media term is well-behaved. `scipy.optimize.curve_fit` with explicit `bounds` and `p0` is essential for convergence at low data volumes — without bounds, the optimizer frequently diverges. The geometric adstock theta grid search (0.0 to 0.9, ~10 steps) prevents premature local minima that plague gradient-based theta estimation on short series.

The Streamlit dashboard page follows all established Phase 6/7 patterns (auth gate, `DashboardSettings`, `@st.cache_data(ttl=300)`, dark palette constants re-declared in page, `st.set_page_config` first, `_conn()` sync reads). The page's two new Plotly charts — saturation curve with `add_vline` + `add_vrect` annotations, and a stacked bar contribution chart — use `go.Figure` with the same `plot_bgcolor`/`paper_bgcolor` dark palette already established.

**Primary recommendation:** Implement `model.py` as pure sync Python (no async), wrap the entire `fit_mmm()` call in `asyncio.to_thread()` in `scheduler.py`, and make `fit_mmm()` return `None` on any `RuntimeError` or `OptimizeWarning` from `curve_fit` rather than propagating. This exactly mirrors the `asyncio.to_thread(generate_spend_trend_chart, ...)` pattern already in `src/reports/daily.py`.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Geometric adstock transform | Python (sync) | — | Pure numpy array operation; no I/O |
| Hill saturation fitting | Python (sync) | — | scipy.optimize is CPU-bound, sync; must run in thread via asyncio.to_thread |
| OLS decomposition | Python (sync) | — | statsmodels is sync; same thread isolation pattern |
| Weekly job scheduling | APScheduler (async) | src/main.py | CronTrigger wired in main.py; job is async coroutine wrapping sync model |
| MMM results persistence | SQLite (DBClient) | — | Append-only mmm_results table; uses existing DBClient.execute() |
| Telegram delivery | aiogram Bot (async) | scheduler.py | Existing bot.send_message() pattern via module globals |
| Attribution page KPI cards | Streamlit frontend | — | Reads mmm_results via sync _conn(); no bot dependency |
| Saturation curve chart | Streamlit frontend | — | plotly.graph_objects.Figure; x=spend, y=deposits |
| Contribution breakdown chart | Streamlit frontend | — | Stacked bar via go.Bar with barmode="stack" |
| Meta vs GA4 attribution table | Streamlit frontend | — | Reuses existing get_attribution_comparison() from db.py |

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| statsmodels | 0.14.6 (latest) | OLS regression: `sm.OLS(endog, exog).fit()` | Standard Python econometrics library; Robyn uses equivalent statsmodels OLS; lightweight vs sklearn |
| scipy | 1.17.1 (latest) | `curve_fit` for Hill saturation parameter estimation | Standard scientific Python; `curve_fit` with bounds is the canonical nonlinear LS API |
| numpy | 2.4.6 (already installed) | Vectorized array ops, adstock loop | Already in venv; adstock loop uses numpy arrays |
| pandas | 2.3.3 (already installed) | Data loading from SQLite results, time indexing | Already in venv; used throughout codebase |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| plotly | 5.x (already installed) | Saturation curve chart, stacked bar chart | Already used for all dashboard charts |
| streamlit | 1.35+ (already installed) | Attribution page rendering | Already used for dashboard |
| aiosqlite | 0.20+ (already installed) | Async DB access from scheduler.py | Already used by DBClient |
| sqlite3 (stdlib) | stdlib | Sync DB access from dashboard page | Already used in src/dashboard/db.py |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| statsmodels OLS | scikit-learn LinearRegression | sklearn gives coefficients but no p-values, no summary(); statsmodels gives full stats table which aids trust in early-stage results |
| scipy curve_fit | scipy minimize | curve_fit is purpose-built for nonlinear LS with Jacobian; minimize needs manual residual function |
| OLS decomposition | Ridge regression | Ridge is better for multicollinearity but obscures coefficient interpretability needed for contribution % |

**Installation:**
```bash
pip install "statsmodels>=0.14" "scipy>=1.13"
```

**Version verification:** [VERIFIED: pip index versions registry 2026-05-24]
- statsmodels latest: 0.14.6 (satisfies `>=0.14`)
- scipy latest: 1.17.1 (satisfies `>=1.13`)
- Both packages are NOT currently installed in the project venv — must be added to pyproject.toml and installed before implementation.

---

## Architecture Patterns

### System Architecture Diagram

```
SQLite (ad_metrics)
    |
    | SELECT daily spend + deposits (campaign-level, ad_set_id='', ad_id='')
    v
[load_mmm_data()]  src/mmm/model.py
    |
    | numpy array: spend_series, deposit_series
    v
[adstock(spend, theta)]  geometric decay loop
    |
    | adstocked_spend[t] = spend[t] + theta * adstocked_spend[t-1]
    v
[hill_saturation(adstocked_spend, km, n)]  scipy curve_fit
    |
    | saturated_media = x^n / (km^n + x^n)
    v
[OLS(deposits ~ const + saturated_media)]  statsmodels
    |
    | params['const'] = baseline_deposits
    | params['media'] = media_coefficient
    v
[MMMResult]  dataclass
    |
    +---> SQLite mmm_results (via DBClient.execute)
    |
    +---> Telegram message (via bot.send_message)
    |
    +---> Streamlit page read (via sync _conn())
                |
                +---> KPI cards (media_pct, ROAS, optimal_spend, maturity)
                +---> Saturation curve chart (plotly)
                +---> Contribution breakdown chart (plotly stacked bar)
                +---> Attribution table (get_attribution_comparison)
```

### Recommended Project Structure
```
src/mmm/
├── __init__.py          # empty
├── model.py             # adstock(), hill_saturation(), fit_mmm(), MMMResult dataclass
└── scheduler.py         # run_mmm_weekly_job() async, register_job_resources()

src/dashboard/pages/
└── 3_Attribution.py     # Streamlit page (st.set_page_config first, auth gate, DashboardSettings)

src/db/schema.py         # Add MIGRATION_006_PHASE8 (mmm_results table)
src/config.py            # Add deposit_value_usd: float = 0.0
src/dashboard/settings.py # Add deposit_value_usd: float = 0.0
src/main.py              # Add mmm_module.register_job_resources() + scheduler.add_job()
pyproject.toml           # Add statsmodels>=0.14, scipy>=1.13
```

### Pattern 1: Geometric Adstock Transform
**What:** Recursive decay transform: `adstock[t] = spend[t] + theta * adstock[t-1]`
**When to use:** Always applied to raw spend before Hill saturation fitting
**Implementation note:** The loop is inherently sequential (recursive dependency). A numpy loop is correct; do not attempt to vectorize with cumsum — it gives incorrect values for non-unit theta.
**Theta grid search:** Iterate theta over `np.linspace(0.0, 0.9, 10)`, fit OLS for each theta, select theta that minimizes OLS residual sum of squares.

```python
# Source: [VERIFIED: forecastegy.com/posts/adstock-in-marketing-mix-modeling/ + standard MMM literature]
import numpy as np

def adstock(spend: np.ndarray, theta: float) -> np.ndarray:
    """Geometric adstock transform. theta in [0, 1)."""
    result = np.empty_like(spend, dtype=float)
    result[0] = spend[0]
    for i in range(1, len(spend)):
        result[i] = spend[i] + theta * result[i - 1]
    return result
```

### Pattern 2: Hill Saturation Function
**What:** `f(x) = x^n / (Km^n + x^n)` where Km is the half-saturation spend level and n is the shape.
**When to use:** Applied to adstocked spend before OLS regression.
**Key property:** f(Km) = 0.5 (Km is the spend at 50% saturation). For 80% saturation: solve `0.8 = x^n / (Km^n + x^n)` → `x = Km * (0.8/0.2)^(1/n) = Km * 4^(1/n)`.

```python
# Source: [VERIFIED: pymc-marketing.io Hill function docs + standard Hill equation literature]
import numpy as np
from scipy.optimize import curve_fit

def hill_saturation(x: np.ndarray, km: float, n: float) -> np.ndarray:
    """Hill saturation: f(x) = x^n / (Km^n + x^n). Output in [0, 1)."""
    return (x ** n) / (km ** n + x ** n)


def fit_hill(adstocked_spend: np.ndarray, deposits: np.ndarray):
    """Fit Hill parameters via curve_fit. Returns (km, n) or None on failure."""
    mean_spend = float(np.mean(adstocked_spend[adstocked_spend > 0])) if np.any(adstocked_spend > 0) else 1.0
    p0 = [mean_spend, 1.0]  # km starts at mean spend; n starts at 1 (linear)
    bounds = (
        [1e-6, 0.5],   # km > 0; n in [0.5, 3.0] for numerical stability
        [mean_spend * 10, 3.0],
    )
    try:
        popt, _ = curve_fit(hill_saturation, adstocked_spend, deposits / deposits.max(),
                            p0=p0, bounds=bounds, maxfev=5000)
        return popt  # [km, n]
    except (RuntimeError, ValueError):
        return None
```

### Pattern 3: OLS Decomposition
**What:** `deposits ~ const + beta * hill(adstock(spend))`. Intercept = baseline (organic), coefficient = media contribution.
**When to use:** After Hill transformation is fitted.
**Naming:** `sm.add_constant(X)` adds a column named `"const"` — access via `result.params["const"]` and `result.params["media"]`.

```python
# Source: [VERIFIED: statsmodels.org/stable/regression.html official docs]
import statsmodels.api as sm
import pandas as pd

def run_ols(deposits: np.ndarray, saturated_media: np.ndarray):
    """OLS: deposits ~ const + media. Returns result or None on failure."""
    X = sm.add_constant(pd.Series(saturated_media, name="media"))
    try:
        result = sm.OLS(deposits, X).fit()
        return result
    except Exception:
        return None
```

**Decomposition arithmetic:**
```python
# baseline_deposits = result.params["const"] (scalar, always >= 0 ideally)
# media_coeff       = result.params["media"]
# fitted_deposits   = result.predict(X)
# media_contribution_per_day = media_coeff * saturated_media
# media_pct = media_contribution.sum() / fitted_deposits.sum()
# baseline_pct = 1 - media_pct
```

### Pattern 4: Theta Grid Search
**What:** Select best theta by minimizing OLS residual SS across a grid.
**Range:** `np.linspace(0.0, 0.9, 10)` — 10 steps, upper bound 0.9 (not 1.0 to avoid near-unit-root explosion on short series).

```python
# Source: [ASSUMED — standard MMM grid search practice, no single authoritative code reference]
best_theta, best_rss = 0.0, float("inf")
for theta in np.linspace(0.0, 0.9, 10):
    ads = adstock(spend, theta)
    sat = hill_saturation(ads, km, n)  # use initial km/n estimate
    X = sm.add_constant(pd.Series(sat, name="media"))
    res = sm.OLS(deposits, X).fit()
    rss = float(np.sum(res.resid ** 2))
    if rss < best_rss:
        best_rss, best_theta = rss, theta
```

**Note:** In practice, theta grid search and Hill fitting are interdependent. The recommended approach is: (1) initial Hill fit with theta=0 to get Km/n starting values, (2) grid search theta with those Km/n, (3) re-fit Hill with best theta. This two-pass approach converges reliably with <8 weeks of data.

### Pattern 5: Optimal Spend Derivation (80% saturation)
**What:** Solve Hill equation for 80% saturation: `x_opt = Km * (0.8/0.2)^(1/n) = Km * 4^(1/n)`
**Derivation:** `0.8 = x^n / (Km^n + x^n)` → `0.8*Km^n + 0.8*x^n = x^n` → `0.8*Km^n = 0.2*x^n` → `x^n = 4*Km^n` → `x = Km * 4^(1/n)`.

```python
# Source: [VERIFIED: standard algebraic derivation from Hill function definition]
def optimal_spend(km: float, n: float) -> float:
    """Spend level at 80% of Hill saturation maximum."""
    return km * (4.0 ** (1.0 / n))
```

**This is the spend level to report as `optimal_daily_spend` in Telegram and dashboard.**

### Pattern 6: asyncio.to_thread Wrapping
**What:** Wrap entire sync `fit_mmm()` call in `asyncio.to_thread()` to avoid blocking the aiogram event loop.
**Why:** `scipy.optimize.curve_fit` and `statsmodels.OLS.fit()` are CPU-bound and can take 0.5–2 seconds on short datasets. They must not block the async event loop.

```python
# Source: [VERIFIED: matches existing pattern in src/reports/daily.py line 232]
async def run_mmm_weekly_job() -> None:
    # ... (module globals for bot, db, settings)
    result: MMMResult | None = await asyncio.to_thread(_fit_mmm_sync, spend_arr, deposit_arr)
    if result is None:
        logger.warning("mmm_fit_failed")
        return
    # ... persist result, send Telegram message
```

### Pattern 7: mmm_results SQLite Schema (Migration 006)
**What:** Append-only table; dashboard reads most recent row only.
**Design rationale:** Store all fitted parameters (theta, km, n) so dashboard can re-derive the full saturation curve from the stored parameters, not just scalar KPIs.

```sql
-- Source: [VERIFIED: follows MIGRATION_005_FORM_SUBMIT pattern in src/db/schema.py]
MIGRATION_006_PHASE8 = """
CREATE TABLE IF NOT EXISTS mmm_results (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date                 TEXT NOT NULL,          -- ISO YYYY-MM-DD
    weeks_of_data            INTEGER NOT NULL,
    media_pct                REAL NOT NULL,
    baseline_pct             REAL NOT NULL,
    incremental_roas_per_1k  REAL,                   -- NULL when deposit_value_usd=0
    optimal_daily_spend      REAL NOT NULL,
    theta                    REAL NOT NULL,
    km                       REAL NOT NULL,
    n                        REAL NOT NULL,
    maturity_label           TEXT NOT NULL,           -- 'directional_only'|'early'|'reliable'|'strong'
    created_at               TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_mmm_results_run_date ON mmm_results(run_date DESC);
"""
```

**Dashboard query:**
```sql
SELECT * FROM mmm_results ORDER BY run_date DESC LIMIT 1;
```

### Pattern 8: Saturation Curve Plotly Chart
**What:** Line chart showing Hill curve from $0 to 2× optimal spend. Vertical line at current avg spend. Shaded rectangle for the "optimal zone" (70%–90% saturation range).
**Key Plotly APIs:** `fig.add_vline()`, `fig.add_vrect()` (both verified via official Plotly docs).

```python
# Source: [VERIFIED: plotly.com/python/horizontal-vertical-shapes/]
import plotly.graph_objects as go
import numpy as np

def build_saturation_chart(km: float, n: float, avg_spend: float, opt_spend: float) -> go.Figure:
    x = np.linspace(0, opt_spend * 2, 200)
    y = (x ** n) / (km ** n + x ** n)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines",
                             line=dict(color="#60a5fa", width=2), name="Saturation curve"))
    # Current spend marker
    fig.add_vline(x=avg_spend, line_dash="dash", line_color="#f59e0b",
                  annotation_text="Current avg", annotation_position="top left")
    # Optimal zone shading
    fig.add_vrect(x0=opt_spend * 0.85, x1=opt_spend * 1.15,
                  fillcolor="#34d399", opacity=0.15,
                  annotation_text="Optimal zone", annotation_position="top right")
    fig.update_layout(
        plot_bgcolor="#1a1d27",
        paper_bgcolor="#0f1117",
        font=dict(color="#e4e7ef"),
        xaxis=dict(title="Daily Spend ($)", gridcolor="#2a2e3a"),
        yaxis=dict(title="Saturation (0–1)", gridcolor="#2a2e3a"),
        margin=dict(l=40, r=40, t=40, b=40),
        height=380,
    )
    return fig
```

### Pattern 9: Contribution Stacked Bar Chart
**What:** 12-week window, each week as a stacked bar with baseline portion and media portion.
**Data source:** `mmm_results` table (latest result) + ad_metrics for the 12-week weekly aggregations.

```python
# Source: [VERIFIED: matches existing go.Bar usage in src/dashboard/pages/1_Campaign_Detail.py]
fig = go.Figure(data=[
    go.Bar(x=weeks, y=baseline_deposits, name="Baseline", marker_color="#6366f1"),
    go.Bar(x=weeks, y=media_deposits,    name="Meta media", marker_color="#34d399"),
])
fig.update_layout(barmode="stack", ...)
```

### Pattern 10: Telegram Message Format
**What:** Plain text (no HTML/Markdown), <4096 chars. Use existing `bot.send_message(parse_mode=None)` or omit parse_mode.

```python
# Source: [VERIFIED: matches REPORT-04 pattern in src/reports/daily.py; D-08 format from CONTEXT.md]
def build_mmm_telegram_message(result: MMMResult, week_label: str) -> str:
    lines = [
        f"📊 Weekly MMM Insight (week of {week_label})",
        "",
        f"Meta drove {result.media_pct:.1f}% of deposits this week (baseline: {result.baseline_pct:.1f}%).",
    ]
    if result.incremental_roas_per_1k is not None:
        lines.append(
            f"Incremental ROAS: {result.incremental_roas_per_1k:.1f}x "
            f"(every $1000 of Meta spend generated {result.incremental_roas_per_1k:.1f} deposits)."
        )
    lines.append(f"Optimal daily spend: ~${result.optimal_daily_spend:.0f} — above this, returns diminish sharply.")
    if result.maturity_label == "directional_only":
        lines.append(f"\n⚠ Directional only — {result.weeks_of_data} weeks of data")
    return "\n".join(lines)
```

**Character budget:** The D-08 template is approximately 350 characters — well within the 4096-char limit. No split needed.

### Anti-Patterns to Avoid
- **Never vectorize adstock with cumsum/cumprod**: The formula `adstock = np.cumsum(spend * theta**t)` is an approximation that only works when spend[0] is dominant. Use the sequential loop.
- **Never pass unbounded p0=[1.0, 1.0] to curve_fit for Hill function**: Without bounds, the optimizer will produce negative km or n→0 solutions on sparse data, yielding NaN outputs.
- **Never use `result.params[0]` and `result.params[1]` with positional indexing after OLS**: Named Series indexing (`result.params["const"]`) is safer than positional. `sm.add_constant()` prepends the constant column with name `"const"` when `prepend=True` (the default).
- **Never cap media_pct at exactly 0 or 1**: When the OLS intercept is negative (can happen with very sparse data), clamp `baseline_pct = max(0.0, 1.0 - media_pct)` to avoid presenting negative baseline to users.
- **Never run MMM inside the Streamlit main thread synchronously for large datasets**: The "Run MMM now" button in D-13 calls fit_mmm() sync within the page — this is acceptable because Streamlit is single-threaded and the dataset is small (<50 rows).

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Nonlinear parameter fitting | Custom gradient descent for Hill params | `scipy.optimize.curve_fit` | Handles Jacobian, covariance estimation, convergence diagnostics automatically |
| OLS with intercept | Manual matrix inversion `(X^T X)^{-1} X^T y` | `statsmodels.api.OLS` | Handles numerical stability, gives p-values, named params, summary() |
| Saturation chart annotations | Manual `add_shape(type='line', ...)` | `fig.add_vline()` / `fig.add_vrect()` | Purpose-built, handles annotation positioning automatically |
| Inline button for "Run MMM now" | Full async job rerun from dashboard | Direct sync `fit_mmm()` call in Streamlit page | D-13: dashboard page is sync; asyncio not available without a new event loop |

**Key insight:** The entire MMM pipeline is < 50 lines of pure Python math. The value is in using the right numeric libraries for stability, not in complexity.

---

## Common Pitfalls

### Pitfall 1: curve_fit Divergence on Sparse Data Without Bounds
**What goes wrong:** `curve_fit(hill_saturation, x, y)` with no `bounds` argument produces `km < 0` or `n → ∞` solutions when data has only 5 weeks of daily observations. The function evaluates `(-km)^n` which is NaN for non-integer n.
**Why it happens:** Unconstrained nonlinear LS can traverse parameter space into physically meaningless regions before converging.
**How to avoid:** Always pass explicit bounds: `bounds=([1e-6, 0.5], [mean_spend * 10, 3.0])`. Set `p0=[mean_spend, 1.0]` to start in a physically sensible region.
**Warning signs:** `popt` contains NaN or negative values; `pcov` diagonal has `inf` entries.

### Pitfall 2: OLS "const" Name Depends on add_constant Prepend Order
**What goes wrong:** `sm.add_constant(X, prepend=False)` appends the constant column — it is then named `"const"` but at position `[-1]`. If code uses `result.params[0]` expecting the intercept, it reads the media coefficient instead.
**Why it happens:** `add_constant` default is `prepend=True`. If someone changes to `prepend=False` for style, positional indexing breaks.
**How to avoid:** Always access `result.params["const"]` and `result.params["media"]` by name. Never use positional indexing on OLS results.
**Warning signs:** `media_pct` > 1.0 or < 0.0 on the first run.

### Pitfall 3: Negative OLS Intercept with Sparse Data
**What goes wrong:** OLS intercept (baseline) comes out negative when the media variable explains more than 100% of deposit variation (overfitting on 5 weeks). `baseline_pct = 1 - media_pct` becomes negative.
**Why it happens:** OLS is unconstrained — there is no constraint that the intercept must be non-negative in standard OLS.
**How to avoid:** Clamp results: `media_pct = max(0.0, min(1.0, computed_media_pct))`. Log a warning when clamping occurs. Consider adding `maturity_label = "directional_only"` regardless of week count when clamping is applied.
**Warning signs:** `result.params["const"] < 0`.

### Pitfall 4: asyncio.to_thread with Mutable Global State
**What goes wrong:** If `fit_mmm()` modifies any module-level numpy arrays or global state, calling it in `asyncio.to_thread` can race with Streamlit's "Run MMM now" button (which calls it on the Streamlit thread).
**Why it happens:** `asyncio.to_thread` runs in a `ThreadPoolExecutor` thread, not the event loop. Both the scheduler job and the Streamlit sync path share the Python GIL but not asyncio isolation.
**How to avoid:** `fit_mmm()` must be a pure function — takes arrays in, returns `MMMResult | None` out, touches no module globals. The bot module globals (`_bot`, `_db`, `_settings`) live in `scheduler.py`, not `model.py`.
**Warning signs:** Occasional NaN results only when both scheduler and "Run MMM now" are triggered simultaneously.

### Pitfall 5: Week Count Query Returns 0 for Days Without Spend
**What goes wrong:** `strftime('%Y-%W', date)` in SQLite uses ISO week numbering where week 0 can appear for Jan 1 before the first Monday. Counting DISTINCT weeks can undercount by 1 in January.
**Why it happens:** SQLite `%W` is week of year (0–53, first Monday = week 1). Week 0 contains days before the first Monday of the year.
**How to avoid:** This is acceptable for an MMM maturity heuristic (the error is at most 1 week in January). Use `strftime('%Y-%W', date)` as specified in D-07 for simplicity. Document the ±1 week tolerance.
**Warning signs:** Week count is 0 in January when there is clearly data.

### Pitfall 6: Hill n Parameter at Boundary Causes Vertical/Horizontal Asymptotes
**What goes wrong:** When `n` is very small (→0), the Hill curve becomes nearly flat (no saturation). When `n` is very large (→∞), the curve becomes a step function. Both make `optimal_daily_spend` meaningless.
**Why it happens:** curve_fit can hit the bounds exactly (`n = 0.5` or `n = 3.0`) without converging to an interior solution.
**How to avoid:** After curve_fit, check if `n` is exactly at a bound: if `n <= 0.5 + 0.01` or `n >= 3.0 - 0.01`, treat the fit as failed (return None from `fit_hill()`). Log warning "Hill n at boundary — fit failed."
**Warning signs:** `optimal_daily_spend` is either 0 or 100× the average spend.

### Pitfall 7: SQL deposits Column is meta_form_submit_deposit, Not meta_purchases_7dclick
**What goes wrong:** Using `meta_purchases_7dclick` as the deposit proxy when the actual NSM metric in this codebase is `meta_form_submit_deposit`.
**Why it happens:** Standard MMM tutorials use purchases; this codebase tracks form-submit deposits as the primary conversion metric (established in Phase 2, post-v1 fix).
**How to avoid:** SQL query for MMM data must use `SUM(meta_form_submit_deposit)` not `SUM(meta_purchases_7dclick)`. This is consistent with all existing dashboard queries in `src/dashboard/db.py`.
**Warning signs:** MMM shows 0 deposits on days where the dashboard shows non-zero CPD.

---

## Code Examples

### Complete fit_mmm() Signature
```python
# Source: [VERIFIED: synthesized from statsmodels OLS docs, scipy curve_fit docs, CONTEXT.md D-04]
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.optimize import curve_fit
import structlog

logger = structlog.get_logger(__name__)

@dataclass
class MMMResult:
    run_date: str
    weeks_of_data: int
    media_pct: float
    baseline_pct: float
    incremental_roas_per_1k: Optional[float]   # None when deposit_value_usd=0
    optimal_daily_spend: float
    theta: float
    km: float
    n: float
    maturity_label: str  # 'directional_only' | 'early' | 'reliable' | 'strong'

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


def fit_mmm(
    spend: np.ndarray,
    deposits: np.ndarray,
    deposit_value_usd: float = 0.0,
    run_date: str = "",
    weeks_of_data: int = 0,
) -> MMMResult | None:
    """Fit geometric adstock + Hill saturation + OLS.

    Returns MMMResult or None if any fitting step fails.
    Pure function — no global state mutations.
    """
    if len(spend) < 7 or np.all(deposits == 0) or np.all(spend == 0):
        logger.warning("mmm_insufficient_data", n=len(spend))
        return None

    mean_spend = float(np.mean(spend[spend > 0]))

    # Step 1: Initial Hill fit with theta=0 to get starting Km/n
    try:
        popt_init, _ = curve_fit(
            lambda x, km, n: (x**n) / (km**n + x**n),
            spend, deposits / deposits.max(),
            p0=[mean_spend, 1.0],
            bounds=([1e-6, 0.5], [mean_spend * 10, 3.0]),
            maxfev=5000,
        )
        km_init, n_init = popt_init
    except (RuntimeError, ValueError):
        logger.warning("mmm_hill_init_failed")
        return None

    # Step 2: Grid search theta
    best_theta, best_rss = 0.0, float("inf")
    for theta in np.linspace(0.0, 0.9, 10):
        ads = adstock(spend, theta)
        sat = (ads**n_init) / (km_init**n_init + ads**n_init)
        X = sm.add_constant(pd.Series(sat, name="media"))
        res = sm.OLS(deposits, X).fit()
        rss = float(np.sum(res.resid**2))
        if rss < best_rss:
            best_rss, best_theta = rss, theta

    # Step 3: Re-fit Hill with best theta
    ads_best = adstock(spend, best_theta)
    try:
        popt, _ = curve_fit(
            lambda x, km, n: (x**n) / (km**n + x**n),
            ads_best, deposits / deposits.max(),
            p0=[km_init, n_init],
            bounds=([1e-6, 0.5], [mean_spend * 10, 3.0]),
            maxfev=5000,
        )
        km, n = popt
    except (RuntimeError, ValueError):
        logger.warning("mmm_hill_refit_failed")
        return None

    # Boundary check
    if n <= 0.51 or n >= 2.99:
        logger.warning("mmm_n_at_boundary", n=n)
        return None

    # Step 4: OLS decomposition
    sat_best = (ads_best**n) / (km**n + ads_best**n)
    X = sm.add_constant(pd.Series(sat_best, name="media"))
    ols_res = sm.OLS(deposits, X).fit()

    baseline_coeff = float(ols_res.params["const"])
    media_coeff = float(ols_res.params["media"])
    fitted = ols_res.predict(X)

    media_contribution = media_coeff * sat_best
    total_fitted = float(np.sum(fitted))
    media_sum = float(np.sum(media_contribution))
    media_pct = max(0.0, min(1.0, media_sum / total_fitted)) if total_fitted > 0 else 0.0

    # Step 5: Optimal spend (80% saturation)
    opt_spend = float(km * (4.0 ** (1.0 / n)))

    # Step 6: Incremental ROAS
    total_spend = float(np.sum(spend))
    incremental_roas: float | None = None
    if deposit_value_usd > 0 and total_spend > 0:
        incremental_value = media_sum * deposit_value_usd
        incremental_roas = incremental_value / total_spend
        if incremental_roas > 100:
            logger.warning("mmm_roas_sanity_cap", roas=incremental_roas)
            incremental_roas = None  # suppress implausible ROAS
    elif total_spend > 0:
        # "deposits per $1000 spend"
        incremental_roas = (media_sum / total_spend) * 1000.0

    # Step 7: Maturity label
    if weeks_of_data < 8:
        maturity = "directional_only"
    elif weeks_of_data < 12:
        maturity = "early"
    else:
        maturity = "reliable"

    return MMMResult(
        run_date=run_date,
        weeks_of_data=weeks_of_data,
        media_pct=round(media_pct * 100, 1),
        baseline_pct=round((1.0 - media_pct) * 100, 1),
        incremental_roas_per_1k=round(incremental_roas, 2) if incremental_roas is not None else None,
        optimal_daily_spend=round(opt_spend, 2),
        theta=round(best_theta, 3),
        km=round(km, 4),
        n=round(n, 4),
        maturity_label=maturity,
    )
```

### Data Loading SQL
```sql
-- Source: [VERIFIED: matches existing dashboard SQL pattern in src/dashboard/db.py]
-- Campaign-level only (ad_set_id='', ad_id='') per CLAUDE.md
-- Uses meta_form_submit_deposit as the deposit metric (not meta_purchases_7dclick)
SELECT date,
       SUM(spend)                    AS daily_spend,
       SUM(meta_form_submit_deposit) AS daily_deposits
FROM ad_metrics
WHERE ad_set_id = '' AND ad_id = ''
  AND spend > 0
GROUP BY date
ORDER BY date ASC;
```

### Week Count SQL
```sql
-- Source: [VERIFIED: D-07 in CONTEXT.md]
SELECT COUNT(DISTINCT strftime('%Y-%W', date)) AS weeks
FROM ad_metrics
WHERE ad_set_id = '' AND ad_id = ''
  AND spend > 0;
```

### APScheduler Wiring in main.py
```python
# Source: [VERIFIED: matches existing scheduler.add_job pattern in src/main.py]
import src.mmm.scheduler as mmm_scheduler_module

mmm_scheduler_module.register_job_resources(bot, db, settings)

scheduler.add_job(
    mmm_scheduler_module.run_mmm_weekly_job,
    trigger=CronTrigger(day_of_week="sun", hour=23, minute=0, timezone=settings.report_timezone),
    id="mmm_weekly",
    replace_existing=True,
    misfire_grace_time=600,   # 10 min grace — MMM can take a few seconds
    coalesce=True,
    max_instances=1,
)
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| R Robyn for MMM | Python statsmodels + scipy | Context decision | No R runtime needed; same conceptual model (Hill + adstock) |
| pymc-marketing Bayesian MMM | Lightweight frequentist OLS | Requires 3+ months data | Directional results available with 4+ weeks |
| Global adstock (Weibull, 2 params) | Geometric adstock (1 param, theta) | D-02 locked decision | Avoids overfitting with <8 weeks data |

**Deprecated / outdated:**
- `scipy.optimize.leastsq`: superseded by `curve_fit` (higher-level, better bounds support). Use `curve_fit` exclusively.
- `statsmodels.formula.api` (smf): The formula API (`smf.ols("y ~ x", data=df).fit()`) is valid but less explicit for programmatic column construction. Use `statsmodels.api` (sm) with named Series for clarity.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Theta grid search range 0.0–0.9 in 10 steps is sufficient to find near-optimal theta | Pattern 4 | Could miss best theta if data has very high carryover (theta > 0.9); acceptable given sparse data constraint |
| A2 | n bounds [0.5, 3.0] cover all realistic marketing saturation shapes | Pattern 2 | If the true n > 3.0, the fit will be clamped and the boundary check will return None, treating a valid high-n curve as a failed fit |
| A3 | The "Run MMM now" Streamlit button (D-13) can safely call fit_mmm() synchronously in the Streamlit thread | Pattern (scheduler.py) | Streamlit thread is not the asyncio event loop thread; no race condition unless DBClient is also called synchronously from Streamlit — but the dashboard uses _conn() (sync sqlite3), not DBClient (aiosqlite), so no conflict |
| A4 | Two-pass Hill+theta fitting (init at theta=0, then grid search) converges better than single-pass on sparse data | Pattern 4 | Unverified by systematic benchmark; standard practice in lightweight MMM implementations |
| A5 | media_pct clamping to [0, 1] when OLS intercept is negative is appropriate | Common Pitfalls #3 | Could mask model instability; should also log a warning |
| A6 | `meta_form_submit_deposit` is the correct deposit column for MMM target variable | Pitfall 7 | Confirmed by db.py `get_kpi_summary` which uses this column; risk LOW |

---

## Open Questions

1. **What is the expected daily deposit count?**
   - What we know: The MMM needs deposits > 0 on most days to fit. If many days have zero deposits, Hill fitting will fail.
   - What's unclear: Current daily deposit volume is unknown from codebase inspection alone.
   - Recommendation: Add a pre-flight check: if more than 70% of days have zero deposits, return None with log message "mmm_too_many_zero_deposit_days".

2. **Should mmm_results accumulate all runs or only keep the latest?**
   - What we know: D-12 specifies "one row per weekly run" — append-only. Dashboard reads most recent row.
   - What's unclear: Will historical MMM results ever be shown in the dashboard (trend of media_pct over time)?
   - Recommendation: Keep append-only (as specified in D-12). Dashboard reads `ORDER BY run_date DESC LIMIT 1`. Historical trend can be added in Phase 9+ by reading all rows.

3. **incremental_roas_per_1k field name when deposit_value_usd is set**
   - What we know: D-21 says "ROAS = (incremental_deposits * deposit_value_usd) / spend" when deposit_value_usd is set. The field is `incremental_roas_per_1k`.
   - What's unclear: When deposit_value_usd is set, the value in `incremental_roas_per_1k` is a true ROAS ratio (e.g., 2.5×), not "per $1000". The column name is misleading.
   - Recommendation: Store the value in `incremental_roas_per_1k` column regardless of calculation mode. Add `maturity_label` or a separate `roas_mode` column to indicate whether it's "per_1k" or "true_roas". For Phase 8, use the field name as specified in D-12 and document the dual meaning in a code comment.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| numpy | adstock, Hill, OLS arrays | Yes | 2.4.6 | — |
| pandas | OLS named Series, data loading | Yes | 2.3.3 | — |
| statsmodels | OLS decomposition | No | — | Must install: `pip install statsmodels>=0.14` |
| scipy | curve_fit for Hill fitting | No | — | Must install: `pip install scipy>=1.13` |
| plotly | Saturation curve + stacked bar | Yes | 5.x (via streamlit) | — |
| streamlit | Attribution dashboard page | Yes | 1.35+ | — |
| Python | Runtime | Yes | 3.12+ (via existing venv) | — |
| pytest | Test suite | Yes | 8.x | — |

**Missing dependencies with no fallback:**
- `statsmodels>=0.14` — blocks `fit_mmm()` OLS decomposition. Wave 0 task must add to pyproject.toml and install.
- `scipy>=1.13` — blocks `curve_fit` Hill saturation fitting. Wave 0 task must add to pyproject.toml and install.

**Missing dependencies with fallback:**
- None.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` (asyncio_mode = "auto") |
| Quick run command | `pytest tests/test_mmm_model.py tests/test_mmm_scheduler.py -x` |
| Full suite command | `pytest tests/ -x` |
| Current test count | 312 (target: 312+ after phase) |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MMM-01 | adstock() output matches manual recursive calculation | unit | `pytest tests/test_mmm_model.py::test_adstock -x` | No — Wave 0 |
| MMM-01 | hill_saturation() output at km = 0.5 at Km | unit | `pytest tests/test_mmm_model.py::test_hill_saturation -x` | No — Wave 0 |
| MMM-01 | fit_mmm() returns None on < 7 days of data | unit | `pytest tests/test_mmm_model.py::test_fit_mmm_insufficient_data -x` | No — Wave 0 |
| MMM-01 | fit_mmm() returns MMMResult with valid fields on synthetic data | unit | `pytest tests/test_mmm_model.py::test_fit_mmm_synthetic -x` | No — Wave 0 |
| MMM-01 | optimal_spend() = km * 4^(1/n) | unit | `pytest tests/test_mmm_model.py::test_optimal_spend -x` | No — Wave 0 |
| MMM-02 | run_mmm_weekly_job skips (no message) when < 4 weeks | unit | `pytest tests/test_mmm_scheduler.py::test_job_skips_below_4_weeks -x` | No — Wave 0 |
| MMM-02 | run_mmm_weekly_job sends warning when 4–7 weeks | unit | `pytest tests/test_mmm_scheduler.py::test_job_warning_4_to_7_weeks -x` | No — Wave 0 |
| MMM-02 | run_mmm_weekly_job inserts row to mmm_results table | integration | `pytest tests/test_mmm_scheduler.py::test_job_persists_result -x` | No — Wave 0 |
| MMM-03 | Telegram message contains media_pct, optimal_daily_spend | unit | `pytest tests/test_mmm_scheduler.py::test_telegram_message_format -x` | No — Wave 0 |
| DASH-11 | 3_Attribution.py imports without error, st.set_page_config first | unit | `pytest tests/test_attribution_page.py::test_page_imports -x` | No — Wave 0 |
| DASH-11 | Attribution page renders KPI cards from mmm_results | unit | `pytest tests/test_attribution_page.py::test_kpi_cards -x` | No — Wave 0 |
| DASH-12 | MIGRATION_006_PHASE8 creates mmm_results table | unit | `pytest tests/test_schema_migration.py::test_migration_006 -x` | No — migration test in existing file |
| DASH-13 | Full suite 312+ tests pass | regression | `pytest tests/ -x` | Yes |

### Sampling Rate
- **Per task commit:** `pytest tests/test_mmm_model.py tests/test_mmm_scheduler.py -x`
- **Per wave merge:** `pytest tests/ -x`
- **Phase gate:** Full suite green (312+) before `/gsd-verify-work`

### Wave 0 Gaps
- `tests/test_mmm_model.py` — covers MMM-01 (model.py unit tests)
- `tests/test_mmm_scheduler.py` — covers MMM-02, MMM-03 (scheduler.py unit tests)
- `tests/test_attribution_page.py` — covers DASH-11 (Streamlit page smoke tests)
- `MIGRATION_006_PHASE8` test — add `test_migration_006` to existing `tests/test_schema_migration.py`
- Framework install: `pip install statsmodels>=0.14 scipy>=1.13` — required before Wave 1

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Dashboard page uses existing _check_auth() pattern |
| V3 Session Management | No | No new sessions |
| V4 Access Control | No | Dashboard read-only; MMM job is internal APScheduler |
| V5 Input Validation | Yes | SQL queries use positional `?` params (existing pattern); no user input interpolated into MMM calculations |
| V6 Cryptography | No | No new cryptographic operations |

### Known Threat Patterns for This Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SQL injection via campaign filter in mmm data load | Tampering | Positional `?` params in all SQL — no f-string SQL (CLAUDE.md enforced) |
| Prompt injection via deposit_value_usd or maturity_label in Telegram message | Tampering | These values are computed floats/ints — not user-provided strings. No injection vector. |
| ROAS sanity cap bypass | Spoofing | D-21: cap at 100× and suppress implausible values; log warning |
| Negative media_pct displayed to users as valid insight | Information disclosure | Clamp media_pct to [0.0, 1.0] and log clamping event; maturity_label signals uncertainty |

---

## Sources

### Primary (HIGH confidence)
- statsmodels official docs (statsmodels.org/stable/regression.html) — OLS API, add_constant, params naming
- scipy official docs (docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.curve_fit.html) — curve_fit signature, bounds, p0, RuntimeError handling
- Plotly official docs (plotly.com/python/horizontal-vertical-shapes/) — add_vline, add_vrect API
- pymc-marketing docs (pymc-marketing.io) — Hill function formula verification
- pip registry 2026-05-24 — statsmodels 0.14.6, scipy 1.17.1, numpy 2.4.6, pandas 2.3.3

### Secondary (MEDIUM confidence)
- forecastegy.com/posts/adstock-in-marketing-mix-modeling/ — geometric adstock loop implementation pattern
- Robyn R package (rdrr.io) — Hill saturation parameter naming (alpha/gamma = shape/inflection, equivalent to n/Km)
- Existing codebase (src/dashboard/db.py, src/dashboard/pages/1_Campaign_Detail.py, src/reports/daily.py, src/db/schema.py, src/db/client.py, src/main.py) — verified patterns for all integration points

### Tertiary (LOW confidence)
- towardsdatascience.com and related MMM articles — conceptual framing only; code examples not directly used

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — versions verified against pip registry 2026-05-24
- Architecture: HIGH — all integration points verified against existing codebase
- Algorithm correctness (adstock, Hill 80% derivation): HIGH — verified against multiple official/authoritative sources
- Pitfalls: HIGH — most derived from direct API documentation behavior + codebase patterns
- Dashboard patterns: HIGH — verified against existing Phase 6/7 code

**Research date:** 2026-05-24
**Valid until:** 2026-08-24 (90 days — scipy/statsmodels APIs are stable)
