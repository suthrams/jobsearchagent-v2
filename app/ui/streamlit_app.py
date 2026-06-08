"""v2 Streamlit UI - thin entrypoint (ADR-088 native multipage).

This file is the page shell only (UI refactor, docs/architecture/ui_refactor_plan.md
+ ADR-088 journey reorg): it sets page config, initializes session state, builds
the journey navigation with Streamlit native multipage (``st.navigation`` /
``st.Page``), renders the shared sidebar (profile selector + filters + Active Run),
and runs the selected page. Each page wraps a view's ``render(ctx)`` through
``app/ui/views/REGISTRY``. It deliberately holds no screen-rendering logic.

Navigation model (ADR-088):
  * Journey groups (FIND / MY OPPORTUNITIES / RESUME + an unlabeled operator rule)
    come from ``nav.NAV_GROUPS``; the sidebar links are rendered by st.navigation.
  * Detail screens (Search detail, Job detail, Live monitor, Run report) are
    ``nav.DESTINATION_VIEWS`` - registered ``visibility="hidden"`` so they route by
    click (via ``_navigate`` -> ``st.switch_page``) but never show in the sidebar.
  * The user-facing labels live in ``nav.DISPLAY_TITLE``; internal view names (the
    REGISTRY keys, the ``_navigate`` targets) stay stable so the rename is one map.

Where everything lives:
  * app/ui/nav.py          - NAV_GROUPS / DESTINATION_VIEWS / DISPLAY_TITLE,
                             ViewContext, register_pages, _navigate
  * app/ui/views/<name>.py - one render(ctx) per screen; REGISTRY maps name -> render
  * app/ui/components/      - shared render helpers (bullets, tailoring card, ...)
  * app/ui/formatting.py    - pure formatters (no st.*); app/ui/data.py - cached reads
  * app/ui/api_client.py    - ALL backend calls (reads + writes) (ADR-075)
"""
# sys.path setup + load_dotenv() must run before the app.* imports below, so those
# imports are intentionally not at the top of the file. E402 suppressed file-wide.
# ruff: noqa: E402
from __future__ import annotations

import re
import sys
from pathlib import Path

# Ensure the project root is on sys.path so `import app.*` works when launched
# via `streamlit run app/ui/streamlit_app.py` (Streamlit puts the script's
# directory on sys.path[0], not the project root).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

import app.ui.api_client as api
import app.ui.nav as nav
from app.ui.nav import _navigate
from app.ui.data import _cached_list_users, _cached_recent_workflows
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
    ("current_user_id", "0"),  # ADR-062: active profile; default = pre-existing data
    ("onboard_step", 1),       # onboarding wizard cursor
    ("onboard_new_user_id", None),
    # Cross-run filter values (ADR-088 Phase 3): the controls render in the Matches
    # view, but the values persist here so _build_ctx() (and New search's threshold
    # default) can read them even when Matches is not the current page.
    ("flt_min_score", 75),
    ("flt_search", ""),
    ("flt_include_excluded", False),
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
            _recent = _cached_recent_workflows()
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


# ── ViewContext from the sidebar filters ──────────────────────────────────────
# Filters render in the sidebar below; each page reads their current values from
# session_state when it runs (after the sidebar is built). Phase 0 keeps them
# always-on; ADR-088 Phase 3 makes them contextual to Matches / Searches.

def _build_ctx() -> nav.ViewContext:
    return nav.ViewContext(
        min_score=int(st.session_state.get("flt_min_score", 75)),
        search=str(st.session_state.get("flt_search", "") or ""),
        include_excluded=bool(st.session_state.get("flt_include_excluded", False)),
    )


# ── Native-multipage navigation (st.navigation / st.Page) ─────────────────────
# Build one page per view. A page is a zero-arg callable that builds the ctx and
# dispatches to the view's render(ctx) through the registry. Internal view names
# stay stable; the user sees nav.DISPLAY_TITLE. Detail screens are hidden pages,
# routed to by _navigate -> st.switch_page.

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "page"


def _page_factory(view_name: str):
    def _page() -> None:
        _render = VIEW_REGISTRY.get(view_name)
        if _render is None:
            st.error(f"No view registered for {view_name!r}. See app/ui/views/.")
            return
        _render(_build_ctx())
    _page.__name__ = _slug(view_name).replace("-", "_")
    return _page


_pages_by_name: dict[str, st.Page] = {}
_grouped: dict[str, list[st.Page]] = {}
for _header, _names in nav.NAV_GROUPS.items():
    _section: list[st.Page] = []
    for _name in _names:
        _pg = st.Page(
            _page_factory(_name),
            title=nav.DISPLAY_TITLE[_name],
            url_path=_slug(nav.DISPLAY_TITLE[_name]),
            default=(_name == nav.DEFAULT_VIEW),
        )
        _pages_by_name[_name] = _pg
        _section.append(_pg)
    _grouped[_header] = _section

# Hidden destinations: registered so they route, but absent from the sidebar. Park
# them in the unlabeled operator section - visibility="hidden" keeps them off-screen
# regardless of section, so this placement is purely structural.
for _name in nav.DESTINATION_VIEWS:
    _pg = st.Page(
        _page_factory(_name),
        title=nav.DISPLAY_TITLE[_name],
        url_path=_slug(nav.DISPLAY_TITLE[_name]),
        visibility="hidden",
    )
    _pages_by_name[_name] = _pg
    _grouped.setdefault(nav.OPERATOR_SECTION, []).append(_pg)

_page = st.navigation(_grouped)
# Let _navigate(...) switch to any view (including hidden destinations) by name.
nav.register_pages(_pages_by_name)


# ── Sidebar (rendered below the native nav) ───────────────────────────────────

with st.sidebar:
    st.title("Job Search Agent v2")

    # ── Profile selector (ADR-062) ───────────────────────────────────────────
    # Picks whose search this is. Re-scopes every history / analytics read and the
    # resume picker, and tags new runs with this owner. No auth - a cooperative
    # selector, not an access boundary (ADR-062 Decision E).
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

    # Cross-run filters render in the Matches view now (ADR-088 Phase 3), not here -
    # they acted only on Matches, so an always-on sidebar copy was inert noise. Their
    # values persist on the flt_* session keys (seeded above) for _build_ctx().
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
        # The Active Run panel is the hub for the per-run destinations (they are
        # click-through, not sidebar entries, under ADR-088). Report is the entry
        # point for the generated report (available once the run completes).
        _b1, _b2, _b3 = st.columns(3)
        if _b1.button("Detail", key="sb_open_detail", use_container_width=True):
            _navigate("Workflow Detail", detail_workflow_id=st.session_state.workflow_id)
        if _b2.button("Live", key="sb_open_live", use_container_width=True):
            _navigate("Live Run Monitor")
        if _b3.button("Report", key="sb_open_report", use_container_width=True):
            _navigate("Run Report")


# ── Run the selected page ─────────────────────────────────────────────────────
# st.navigation resolved the current page from the URL / nav click (or the default,
# Matches). Running it dispatches to that view's render(_build_ctx()).
_page.run()
