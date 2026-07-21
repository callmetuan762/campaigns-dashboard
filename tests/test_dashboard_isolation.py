"""DASH-05: src/dashboard/* must be fully standalone (no aiogram, no src.ai)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

DASHBOARD_DIR = Path("src/dashboard")
FORBIDDEN_MODULES_ALL = {
    "aiogram", "aiosqlite", "asyncio",
}
# These are forbidden EVERYWHERE in src/dashboard (D-19, D-05).
FORBIDDEN_PREFIXES_ALL = (
    "src.ai", "src.bot", "src.meta", "src.ga4", "src.reports",
)
# streamlit is allowed only in app.py, plus components.py (Phase D shared UI
# tier -- render_scope_line/render_reconciliation_block are used by Overview.py
# and by pages/*.py, so they live at the top level like db.py/settings.py, but
# unlike those data-tier modules they intentionally render Streamlit widgets).
STREAMLIT_ALLOWED_FILES = {"Overview.py", "components.py"}


def _collect_imports(py_path: Path) -> set[str]:
    tree = ast.parse(py_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
    return names


def test_dashboard_files_have_no_forbidden_imports() -> None:
    files = sorted(DASHBOARD_DIR.glob("*.py"))
    assert files, "no src/dashboard/*.py files found"
    for f in files:
        imports = _collect_imports(f)
        for mod in imports:
            for forbidden in FORBIDDEN_MODULES_ALL:
                assert mod != forbidden and not mod.startswith(forbidden + "."), (
                    f"{f.name} imports forbidden module {mod!r}"
                )
            for prefix in FORBIDDEN_PREFIXES_ALL:
                assert not mod.startswith(prefix), (
                    f"{f.name} imports forbidden tier {mod!r}"
                )


def test_streamlit_only_imported_from_app_py() -> None:
    for f in sorted(DASHBOARD_DIR.glob("*.py")):
        imports = _collect_imports(f)
        has_streamlit = any(m == "streamlit" or m.startswith("streamlit.") for m in imports)
        if f.name in STREAMLIT_ALLOWED_FILES:
            assert has_streamlit, "app.py is expected to import streamlit"
        else:
            assert not has_streamlit, (
                f"{f.name} must not import streamlit (data/tools/chat tier)"
            )


def test_no_async_anthropic_anywhere() -> None:
    for f in sorted(DASHBOARD_DIR.glob("*.py")):
        text = f.read_text(encoding="utf-8")
        assert "AsyncAnthropic" not in text, f"{f.name} uses AsyncAnthropic — must be sync"
