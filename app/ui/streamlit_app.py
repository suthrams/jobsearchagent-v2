"""v2 Streamlit UI.

Layout (sidebar order is intentional — top of the page first):

  * Workflow History (default landing) — list of all runs, click to drill in
  * Workflow Detail                    — per-run unified view: jobs, scores,
                                         deep review, advice, prep, settings
                                         used for that run, constraints hit
  * Start New Run                      — settings inline + custom URLs textarea
  * Live Run Monitor                   — activity feed for the currently running run
  * Run Report                         — generated markdown report
  * Settings                           — view + edit user-overridable config
  * (Cross-run analytics — Top Matches, IC/Architect/Mgmt Track, Companies)

Browse views read data/v2.db directly via db_reader.py.
Control actions (start workflow, edit config) call FastAPI via api_client.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is on sys.path so `import app.*` works when launched
# via `streamlit run app/ui/streamlit_app.py` (Streamlit puts the script's
# directory on sys.path[0], not the project root).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# The entrypoint is a thin control surface (UI refactor, ui_refactor_plan.md): it
# wires page config + session state + the sidebar, then dispatches the selected
# view through the registry. All screen rendering lives in app/ui/views/; shared
# helpers in formatting.py / components/ / data.py. So it imports very little.
import app.ui.api_client as api
import app.ui.nav as nav
from app.ui.nav import _navigate
from app.ui.data import _cached_list_users
from app.ui.db_reader import load_recent_workflows
from app.ui.views import REGISTRY as VIEW_REGISTRY


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Job Search Agent v2",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session state ─────────────────────────────────────────────────────────────

for _key, _default in (
    ("workflow_id", None),
    ("last_status", None),
    ("last_response", None),
    ("detail_workflow_id", None),
    ("detail_job_id", None),
    ("config_cache", None),
    ("sidebar_view", "Workflow History"),
    ("current_user_id", "0"),  # ADR-062: active profile; default = pre-existing data
    ("onboard_step", 1),       # onboarding wizard cursor
    ("onboard_new_user_id", None),
):
    if _key not in st.session_state:
        st.session_state[_key] = _default

# ADR-062: mirror the active profile onto the API client before any request fires
# this rerun. The sidebar selector may change it below, after which we re-set it.
api.set_user_id(st.session_state.current_user_id)


# Auto-reconnect to the most recent workflow on first load.
# Failures are stored on session_state so the sidebar can surface them as a caption
# instead of the user wondering why "Active Run" is empty.
if "workflow_reconnect_attempted" not in st.session_state:
    st.session_state.workflow_reconnect_attempted = True
    st.session_state.workflow_reconnect_error = None
    if st.session_state.workflow_id is None:
        try:
            _recent = load_recent_workflows()
            if not _recent.empty and "workflow_id" in _recent.columns:
                _reconnect_id = _recent.iloc[0]["workflow_id"]
                st.session_state.workflow_id = _reconnect_id
                try:
                    _reconnect_resp = api.get_workflow_status(_reconnect_id)
                    st.session_state.last_status = _reconnect_resp.get("status")
                    st.session_state.last_response = _reconnect_resp
                except Exception as exc:
                    st.session_state.workflow_reconnect_error = (
                        f"Could not fetch status for the most-recent run: {exc}"
                    )
        except Exception as exc:
            st.session_state.workflow_reconnect_error = (
                f"Could not load recent workflows from the database: {exc}"
            )


# ── Flush pending navigation before the radio widget is created ───────────────
# _navigate() cannot write sidebar_view after the widget is instantiated, so it
# stores the destination in _pending_nav and we apply it here on the next cycle.
if st.session_state.get("_pending_nav"):
    st.session_state.sidebar_view = st.session_state.pop("_pending_nav")

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("Job Search Agent v2")

    # ── Profile selector (ADR-062) ───────────────────────────────────────────
    # Picks whose search this is. Re-scopes every history / analytics read and
    # the resume picker, and tags new runs with this owner. No auth — this is a
    # cooperative selector, not an access boundary (ADR-062 Decision E).
    _users = _cached_list_users()
    if _users:
        _id_to_label = {str(u["id"]): f"{u['name']}  (#{u['id']})" for u in _users}
        _ids = list(_id_to_label.keys())
        _cur = st.session_state.current_user_id
        if _cur not in _ids:
            _cur = _ids[0]
        _chosen = st.selectbox(
            "Profile",
            _ids,
            index=_ids.index(_cur),
            format_func=lambda i: _id_to_label.get(i, i),
            key="_profile_select",
            help="Whose search this is. Switching re-scopes history, analytics, "
                 "and the resume picker to that profile.",
        )
        if _chosen != st.session_state.current_user_id:
            st.session_state.current_user_id = _chosen
            api.set_user_id(_chosen)
            st.cache_data.clear()
            st.session_state.config_cache = None
            st.rerun()
        _note = next((u.get("note") for u in _users if str(u["id"]) == _chosen), None)
        if _note:
            st.caption(_note)
    else:
        st.caption("No profiles found (backend offline?).")
    if st.button("＋ Add profile", use_container_width=True):
        st.session_state.onboard_step = 1
        st.session_state.onboard_new_user_id = None
        _navigate("Profiles")

    st.markdown("---")
    # View list + (later) dispatch live in app/ui/nav.py — the single source of
    # truth for the UI refactor (docs/architecture/ui_refactor_plan.md, Phase 0).
    view = st.radio(
        "View",
        nav.NAV_ITEMS,
        key="sidebar_view",
    )
    st.markdown("---")
    min_score = st.slider(
        "Minimum match score",
        min_value=0, max_value=100, value=75, step=5,
        help="Jobs with any track score (technical / architecture / leadership) at or above "
             "this value qualify for deep review and interview prep.",
    )
    st.markdown("---")
    search = st.text_input("Search title / company", placeholder="e.g. Staff Engineer")
    include_excluded = st.checkbox(
        "Include excluded jobs",
        value=False,
        help="ADR-057: jobs you've explicitly excluded are hidden from cross-run "
             "analytics by default. Tick to surface them.",
    )
    st.markdown("---")
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.session_state.config_cache = None
        st.rerun()
    if st.session_state.get("workflow_reconnect_error"):
        st.caption(f"⚠ {st.session_state.workflow_reconnect_error}")
    if st.session_state.workflow_id:
        _wst = st.session_state.last_status or "unknown"
        _wicon = {
            "running": "🔵", "waiting_for_user": "🟡",
            "completed": "🟢", "failed": "🔴",
        }.get(_wst, "⚪")
        st.markdown("---")
        st.markdown(f"**Active Run** {_wicon} `{_wst}`")
        st.caption(f"`{st.session_state.workflow_id[:12]}…`")
        _wresp = st.session_state.last_response or {}
        _wstep = _wresp.get("current_step")
        if _wstep:
            st.caption(f"Step: `{_wstep}`")
        _wm = _wresp.get("run_metrics") or {}
        if _wm.get("llm_calls"):
            st.caption(f"{_wm['llm_calls']} calls · ${_wm.get('estimated_cost_usd', 0):.4f}")
        _b1, _b2 = st.columns(2)
        if _b1.button("Detail", key="sb_open_detail", use_container_width=True):
            _navigate("Workflow Detail", detail_workflow_id=st.session_state.workflow_id)
        if _b2.button("Live", key="sb_open_live", use_container_width=True):
            _navigate("Live Run Monitor")

if view == nav.SEPARATOR:
    st.info("Select a view from the sidebar.")
    st.stop()

# ── Registry dispatch (UI refactor Phase 3) ───────────────────────────────────
# Views migrated into app/ui/views/ render here via REGISTRY[view](ctx); the
# legacy if/elif chain below handles the rest until they migrate too. ctx carries
# the sidebar filter widgets; workflow_id / current_user_id stay on session_state.
ctx = nav.ViewContext(min_score=min_score, search=search, include_excluded=include_excluded)
if view in VIEW_REGISTRY:
    VIEW_REGISTRY[view](ctx)
    st.stop()

