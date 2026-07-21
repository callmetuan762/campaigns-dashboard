"""Smoke tests for src/dashboard/pages/8_Tracking_Health.py (Phase C).

Source-level (no Streamlit runtime) tests mirroring tests/test_attribution_page.py:
  - File exists and parses cleanly (no syntax errors)
  - st.set_page_config is the first st.* call (Streamlit rule)
  - No banned imports (aiogram / src.bot / src.ai — D-19 standalone page rule)
  - Required elements present (DB helpers, banding helpers, anomaly detector, runbook)
  - Palette constants declared inline (D-19)
"""
from __future__ import annotations

import ast
import tokenize
from io import StringIO
from pathlib import Path

PAGE_PATH = Path("src/dashboard/pages/8_Tracking_Health.py")


def test_page_file_exists() -> None:
    assert PAGE_PATH.exists(), f"8_Tracking_Health.py not found at {PAGE_PATH}"


def test_page_syntax_valid() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    ast.parse(source)  # raises SyntaxError on bad code


def test_page_set_page_config_is_first_st_call() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "st.set_page_config" in source, "Page missing st.set_page_config call"

    tokens = list(tokenize.generate_tokens(StringIO(source).readline))
    first_st_attr_lineno: int | None = None
    config_lineno: int | None = None

    for i, tok in enumerate(tokens):
        if tok.type != tokenize.NAME or tok.string != "st":
            continue
        if i + 2 < len(tokens):
            dot = tokens[i + 1]
            attr = tokens[i + 2]
            if dot.type == tokenize.OP and dot.string == "." and attr.type == tokenize.NAME:
                if first_st_attr_lineno is None:
                    first_st_attr_lineno = tok.start[0]
                if attr.string == "set_page_config" and config_lineno is None:
                    config_lineno = tok.start[0]

    assert first_st_attr_lineno is not None, "No st.* attribute access found"
    assert config_lineno is not None, "No st.set_page_config token found"
    assert config_lineno == first_st_attr_lineno, (
        f"st.set_page_config is at line {config_lineno} but the first st.* "
        f"call is at line {first_st_attr_lineno} — set_page_config must be first."
    )


def test_no_banned_imports() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    banned = ["aiogram", "src.bot", "src.ai"]

    tree = ast.parse(source)
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.append(node.module)

    for ban in banned:
        for mod in imported_modules:
            assert not mod.startswith(ban), (
                f"Banned import '{mod}' (starts with {ban!r}) found in 8_Tracking_Health.py"
            )


def test_required_elements_present() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    required = [
        # DB layer
        "get_click_session_ratio",
        "get_purchase_divergence",
        "get_not_set_campaign_share",
        "get_event_freshness_hours",
        "get_event_daily_counts",
        "get_sessions_daily",
        "get_pixel_health",
        # Banding helpers
        "click_session_ratio_color",
        "freshness_color",
        "not_set_share_color",
        # Shared anomaly detector (reused, not reimplemented)
        "find_anomalies_in_range",
        "CRITICAL_EVENTS",
        # CLAUDE.md data model rule — Never blend Meta/GA4
        "never blended",
        # Runbook
        "Runbook",
        # Dark palette constants
        "COLOR_BG_PAPER",
        # Caching + settings
        "st.cache_data",
        "DashboardSettings",
        "render_scope_line",
        # EMQ research-outcome caption
        "n/a",
    ]
    missing = [r for r in required if r not in source]
    assert not missing, f"Missing required elements in 8_Tracking_Health.py: {missing}"


def test_palette_constants_declared() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    for const in ["COLOR_BG_PAPER", "COLOR_BG_PLOT", "COLOR_FONT", "COLOR_GRID"]:
        assert f"{const} = " in source, (
            f"Palette constant {const} not declared inline (D-19 rule)"
        )


def test_page_set_page_config_called_with_layout_wide() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert 'layout="wide"' in source or "layout='wide'" in source


def test_page_empty_state_messages_present() -> None:
    """Every empty/no-data table must degrade to a friendly message (spec requirement)."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "No GA4 event data yet" in source
    assert "No pixel_health data yet" in source


def test_page_never_blends_meta_ga4() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "compute_gap_pct" in source
    assert "gap_chip_color" in source
