"""Smoke tests for src/dashboard/pages/3_Attribution.py (DASH-11).

Source-level (no Streamlit runtime) tests verifying:
  - File exists and parses cleanly (no syntax errors)
  - st.set_page_config is the first st.* call (Streamlit rule)
  - No banned imports (aiogram / src.bot / src.ai — D-19 standalone page rule)
  - All required elements present (DB helpers, palette constants, chart APIs)
  - Palette constants are declared, not just referenced (D-19)
"""
from __future__ import annotations

import ast
import tokenize
from io import StringIO
from pathlib import Path

PAGE_PATH = Path("src/dashboard/pages/3_Attribution.py")


def test_page_file_exists() -> None:
    assert PAGE_PATH.exists(), f"3_Attribution.py not found at {PAGE_PATH}"


def test_page_syntax_valid() -> None:
    """Python source must parse — catches accidental syntax breaks."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    ast.parse(source)  # raises SyntaxError on bad code


def test_page_set_page_config_is_first_st_call() -> None:
    """st.set_page_config must be the first st.* call in the file (Streamlit rule).

    Uses tokenize to skip docstring and comment occurrences of 'st.' so this is
    robust against documentation mentions like '... no st.cache_data ...' etc.
    """
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "st.set_page_config" in source, "Page missing st.set_page_config call"

    # Walk real NAME tokens (skips strings/comments) to find the first 'st' identifier
    # followed by a '.' attribute access.
    tokens = list(tokenize.generate_tokens(StringIO(source).readline))
    first_st_attr_lineno: int | None = None
    config_lineno: int | None = None

    for i, tok in enumerate(tokens):
        if tok.type != tokenize.NAME or tok.string != "st":
            continue
        # Look ahead for '.' then identifier
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
    """Standalone page rule (D-19): page must NOT import bot framework modules."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    banned = ["aiogram", "src.bot", "src.ai"]

    # Parse import statements only — substring search would false-positive on
    # docstring mentions of the banned names.
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
                f"Banned import '{mod}' (starts with {ban!r}) found in 3_Attribution.py"
            )


def test_required_elements_present() -> None:
    """DASH-11/DASH-12/DASH-13: page must wire all expected DB calls and chart APIs."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    required = [
        # DB layer (08-01 wired these)
        "get_latest_mmm_result",
        "get_weekly_contributions",
        "get_attribution_comparison",
        # CLAUDE.md data model rule — Never blend Meta/GA4 caption
        "Never blend",
        # Dark palette constants
        "COLOR_BG_PAPER",
        # Plotly saturation curve markers
        "add_vline",
        "add_vrect",
        # Stacked contribution bar
        "barmode",
        # D-13 empty-state UX
        "Run MMM now",
        "MMM has not run yet",
        # Caching + settings
        "st.cache_data",
        "DashboardSettings",
    ]
    missing = [r for r in required if r not in source]
    assert not missing, f"Missing required elements in 3_Attribution.py: {missing}"


def test_palette_constants_declared() -> None:
    """D-19 standalone page rule: dark palette constants must be declared in the page,
    not imported from app.py.
    """
    source = PAGE_PATH.read_text(encoding="utf-8")
    for const in ["COLOR_BG_PAPER", "COLOR_BG_PLOT", "COLOR_FONT", "COLOR_GRID"]:
        # Must be assigned (defined), not just referenced. `<NAME> = ` is the
        # canonical assignment pattern; whitespace and quoting around the rhs
        # are checked by the syntax test above.
        assert f"{const} = " in source, (
            f"Palette constant {const} not declared inline (D-19 rule)"
        )


def test_page_set_page_config_called_with_layout_wide() -> None:
    """D-11 layout: page uses wide layout for the two-row chart grid."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert 'layout="wide"' in source or "layout='wide'" in source, (
        "3_Attribution.py must call st.set_page_config(layout='wide')"
    )


def test_page_uses_get_attribution_comparison_window() -> None:
    """Row 3 attribution table reads via get_attribution_comparison with a date window."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    # The page is responsible for computing a date window; verify the
    # function is actually called (not just imported/mentioned).
    assert "get_attribution_comparison(" in source or "_cached_attribution(" in source, (
        "Page must call get_attribution_comparison (directly or via cache wrapper)"
    )
