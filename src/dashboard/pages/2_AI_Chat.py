"""Dedicated AI Chat page (DASH-08).

Full-screen, distraction-free conversational surface. Reuses run_chat_3agent
from src/dashboard/chat.py (added in 07-04). Auth gate is duplicated from
app.py per Phase 6 D-19 standalone rule (each Streamlit page is its own script).

History key: st.session_state.chat_page_history -- INDEPENDENT from the Overview
page's st.session_state.chat_history so each surface has its own conversation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

st.set_page_config(
    page_title="AI Chat — Ads Performance",
    layout="wide",
    initial_sidebar_state="expanded",
)

from src.dashboard import chat as chat_mod          # noqa: E402
from src.dashboard import db                        # noqa: E402
from src.dashboard.settings import DashboardSettings  # noqa: E402

# Dark-theme palette — duplicated from app.py per D-19 standalone rule
COLOR_BG_PAPER = "#0f1117"
COLOR_BG_PLOT = "#1a1d27"
COLOR_FONT = "#e4e7ef"
COLOR_GRID = "#2a2e3a"


# Auth gate — duplicated from app.py per D-19 (each page is its own script)
def _check_auth(password_required: str) -> bool:
    if not password_required:
        return True
    if st.session_state.get("authenticated"):
        return True
    st.title("AI Chat")
    st.caption("Sign in to continue.")
    with st.form("auth_form_chat"):
        pw = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
        if submitted:
            if pw == password_required:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password")
    return False


settings = DashboardSettings()  # type: ignore[call-arg]

if not _check_auth(settings.dashboard_password):
    st.stop()

db_path = settings.db_path
if not db_path.exists():
    st.error(
        f"Database not found at `{db_path}`. "
        "Run the bot once to ingest data, or set DB_PATH in your .env file."
    )
    st.stop()

db_path_str = str(db_path)
api_key = settings.anthropic_api_key or ""

# Independent history per user instruction — NEVER touches Overview's chat_history key
if "chat_page_history" not in st.session_state:
    st.session_state.chat_page_history = []

# ---------------------------------------------------------------------------
# Sidebar — clear button + data freshness context
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("AI Chat")
    st.page_link("app.py", label="← Back to Overview")

    st.divider()

    if st.button("Clear conversation", use_container_width=True):
        st.session_state.chat_page_history = []
        st.rerun()

    st.divider()

    # Data freshness — gives user context for what data the AI can see
    try:
        fresh = db.get_data_freshness(Path(db_path_str))
        st.caption("**Data freshness**")
        st.caption(f"Meta last date: `{fresh.get('meta_last_date') or '—'}`")
        st.caption(f"GA4 last date:  `{fresh.get('ga4_last_date') or '—'}`")
    except Exception:  # noqa: BLE001
        st.caption("Data freshness unavailable.")

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
st.title("AI Chat")
st.caption(
    "Ask about campaign performance — answers use the 3-agent architecture "
    "(MetaAgent + GA4Agent + AttributionAgent) for higher accuracy."
)

if not api_key:
    st.info("AI chat unavailable — `ANTHROPIC_API_KEY` is not set in `.env`.")

# ---------------------------------------------------------------------------
# Chat history render (string-content turns only — D-20: agent tool traces
# are not persisted into chat_page_history)
# ---------------------------------------------------------------------------
for msg in st.session_state.chat_page_history:
    role = msg.get("role")
    content = msg.get("content")
    if role not in ("user", "assistant"):
        continue
    if isinstance(content, str):
        st.chat_message(role).markdown(content)
    # list-content (tool_use / tool_result) is internal; do not render

# ---------------------------------------------------------------------------
# Chat input handler
# ---------------------------------------------------------------------------
if prompt := st.chat_input("Ask about campaign performance, ROAS, deposits…"):
    if not api_key:
        st.error("Cannot send: `ANTHROPIC_API_KEY` is not configured.")
    else:
        st.chat_message("user").markdown(prompt)
        with st.spinner("Thinking…"):
            final_text, new_history = chat_mod.run_chat_3agent(
                user_text=prompt,
                history=st.session_state.chat_page_history,
                db_path=db_path_str,
                api_key=api_key,
                settings=settings,
            )
        st.chat_message("assistant").markdown(final_text)
        st.session_state.chat_page_history = new_history
