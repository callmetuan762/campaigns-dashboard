"""Source-level smoke tests for the Preorder Funnel (v3) section added to
src/dashboard/pages/3_Funnel.py.

Follows the same no-Streamlit-runtime pattern as tests/test_attribution_page.py:
importing 3_Funnel.py directly would execute st.set_page_config() and hit live
DB calls against settings.db_path at module scope, so these tests parse the
source instead of importing it.
"""
from __future__ import annotations

import ast
from pathlib import Path

PAGE_PATH = Path("src/dashboard/pages/3_Funnel.py")


def test_page_file_exists() -> None:
    assert PAGE_PATH.exists()


def test_page_syntax_valid() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    ast.parse(source)


def test_no_banned_imports() -> None:
    """D-19 standalone page rule: no bot-framework / asyncio imports."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)

    banned = ["aiogram", "src.bot", "src.ai", "asyncio"]
    for ban in banned:
        for mod in imported_modules:
            assert not mod.startswith(ban), f"Banned import '{mod}' found in 3_Funnel.py"


def test_new_section_present() -> None:
    """The new top-of-page v3 section and its required sub-parts exist."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    required = [
        "Preorder Funnel (v3)",
        "Click → Session Gap",
        "Segment comparison",
        "Quiz Funnel",
        "(not set)",
        "_cached_funnel_steps",
        "_cached_click_gap",
        "_cached_not_set_share",
        "_cached_segment_funnels",
        "_cached_quiz_funnel",
        "_cached_quiz_cpl",
    ]
    missing = [r for r in required if r not in source]
    assert not missing, f"Missing required elements: {missing}"


def test_new_section_precedes_legacy_nsm_section() -> None:
    """New v3 section must render above (earlier in the file than) the legacy
    NSM Two-Gate section, per the funnel-v3 spec."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    v3_idx = source.index("Preorder Funnel (v3)")
    legacy_idx = source.index("NSM Two-Gate Framework")
    assert v3_idx < legacy_idx, "v3 section must appear before the legacy NSM section"


def test_new_section_precedes_legacy_empty_state_stop() -> None:
    """The new section must render even when the legacy Stripe funnel has no
    data -- i.e. it must appear before the legacy empty-state `st.stop()`
    guard (not the earlier, unrelated password-auth-gate `st.stop()`)."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    v3_idx = source.index("Preorder Funnel (v3)")
    empty_state_idx = source.index("No Stripe data yet")
    stop_idx = source.index("st.stop()", empty_state_idx)
    assert v3_idx < empty_state_idx < stop_idx


def test_legacy_sections_caption_present() -> None:
    """A caption must tell the viewer the sections below are the legacy funnel."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "Legacy deposit-funnel sections below" in source


def test_data_honesty_caveats_present() -> None:
    """The required data-honesty captions must be present verbatim-ish."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "auto-redirect" in source
    assert "never averaged" in source or "never blend" in source.lower()
    assert "n/a" in source


def test_quiz_lp_slugs_constant_declared() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "QUIZ_LP_SLUGS = " in source
    assert "routine-break" in source
    assert "big-feelings-type" in source
    assert "screen-kid" in source


def test_db_module_used_for_band_helpers() -> None:
    """The gap / not-set-share color bands must come from the tested db.py
    pure functions, not be re-implemented ad hoc in the page.

    D-11 fix: the old single-metric click_session_gap_band chip was replaced by
    the capture-gap / attribution-gap decomposition — the page now calls the two
    new band helpers instead (click_session_gap_band is kept in db.py only for
    backward compatibility with the legacy gap_clicks_pct/gap_lpv_pct fields)."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "db.capture_gap_band(" in source
    assert "db.attribution_gap_band(" in source
    assert "db.not_set_share_band(" in source


def test_click_session_gap_decomposition_present() -> None:
    """The 4-step decomposition and its two separately-labeled gaps must be
    present in the page (D-11 fix — capture loss vs attribution loss)."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    required = [
        "GA4 Sessions (all)",
        "Campaign-Attributed Sessions",
        "Capture Gap",
        "Attribution Gap",
        "ga4_sessions_all",
        "ga4_sessions_attributed",
        "capture_gap_pct",
        "attribution_gap_pct",
    ]
    missing = [r for r in required if r not in source]
    assert not missing, f"Missing required decomposition elements: {missing}"
