"""Run-status component (ADR-089): the state-aware "your search" surface.

Rendered two ways:
  * ``render_run_status("matches")`` - the full strip at the top of Matches. While a
    run is ``running`` it lives in an ``st.fragment(run_every=5s)`` that re-polls
    status and, on completion, clears caches + reruns the whole app so results appear
    inline (closes ADR-088 UX-review R-7). In every other state it renders a static
    strip.
  * ``render_run_status("sidebar")`` - a slim status chip so the run state is visible
    from any screen, with a single contextual jump.

Job-seeker words, the right action per state, and NO application tracking
(ADR-088 E): the strip offers preparation + navigation only - never Apply/Save/status
or a pursuing/shortlist/saved set.
"""
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

import app.ui.api_client as api
from app.ui.nav import _navigate

# Workflow step -> a phrase a job seeker understands. Unmapped steps are humanized
# (underscores to spaces); research/review/advice/prep variants fall through to that.
_STEP_LABEL = {
    "register_run": "starting up",
    "load_resume": "reading your resume",
    "discover_jobs": "finding jobs",
    "relevance_filter": "checking relevance",
    "await_scoring_selection": "waiting for your picks",
    "score_jobs": "scoring jobs against your resume",
    "await_job_selection": "selecting the best fits",
    "deep_review_gate": "deep review",
    "generate_report": "writing the summary",
}

_DONE = ("completed", "completed_with_errors")
_CHIP_ICON = {"running": "🔵", "completed": "🟢", "completed_with_errors": "🟢",
              "failed": "🔴", "awaiting_scoring_selection": "🟡"}
_CHIP_LABEL = {"running": "Search running", "completed": "Last search done",
               "completed_with_errors": "Last search done", "failed": "Search failed",
               "awaiting_scoring_selection": "Needs your picks"}


def _friendly_step(step: str | None) -> str:
    if not step:
        return "working"
    s = str(step)
    if s in _STEP_LABEL:
        return _STEP_LABEL[s]
    return s.split("[")[0].replace("_", " ").strip() or "working"


def _elapsed(started_at) -> str | None:
    if not started_at:
        return None
    try:
        ts = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        secs = int((datetime.now(timezone.utc) - ts).total_seconds())
        if secs < 0:
            return None
        m, s = divmod(secs, 60)
        return f"{m}:{s:02d}"
    except Exception:  # noqa: BLE001
        return None


# ── Public entry ──────────────────────────────────────────────────────────────

def render_run_status(location: str) -> None:
    wf_id = st.session_state.get("workflow_id")
    status = st.session_state.get("last_status")
    if location == "sidebar":
        _chip(wf_id, status)
        return
    # Matches: full strip. Auto-refresh ONLY while running.
    if wf_id and status == "running":
        st.fragment(_running_strip, run_every=5)()
    else:
        _static_strip(wf_id, status)


# ── Running (auto-refreshing) ─────────────────────────────────────────────────

def _running_strip() -> None:
    wf_id = st.session_state.get("workflow_id")
    try:
        resp = api.get_workflow_status(wf_id)
    except Exception:  # noqa: BLE001 - keep the last known state on a poll hiccup
        resp = st.session_state.get("last_response") or {}
    new_status = resp.get("status") or "running"
    st.session_state.last_status = new_status
    st.session_state.last_response = resp
    if new_status != "running":
        # The run left 'running' - refresh the whole app so the strip switches state
        # and the matches table shows the new scores.
        st.cache_data.clear()
        st.rerun(scope="app")

    rm = resp.get("run_metrics") or {}
    bits = [_friendly_step(resp.get("current_step"))]
    el = _elapsed(rm.get("started_at"))
    if el:
        bits.append(el)
    if rm.get("llm_calls"):
        bits.append(f"{rm['llm_calls']} calls · ${rm.get('estimated_cost_usd', 0):.4f}")
    left, right = st.columns([3, 2], vertical_alignment="center")
    left.markdown("🔵 **Search running** — " + "  ·  ".join(bits))
    with right:
        w, c = st.columns(2)
        if w.button("Watch ▶", key="rs_watch", use_container_width=True):
            _navigate("Live Run Monitor")
        if c.button("Cancel", key="rs_cancel", use_container_width=True):
            try:
                api.cancel_workflow(wf_id)
                st.toast("Cancellation requested — it stops at the next step.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Cancel failed: {exc}")
    st.divider()


# ── Static (idle / awaiting / done / failed) ──────────────────────────────────

def _new_search_button(key: str, *, primary: bool = True) -> None:
    if st.button("➕ New search", key=key, use_container_width=True,
                 type="primary" if primary else "secondary"):
        _navigate("Start New Run")


def _static_strip(wf_id: str | None, status: str | None) -> None:
    left, right = st.columns([3, 2], vertical_alignment="center")

    if not wf_id:
        left.caption("No search yet — find roles scored against your resume.")
        with right:
            _new_search_button("rs_new_idle")
        st.divider()
        return

    if status == "awaiting_scoring_selection":
        left.markdown("🟡 **Your search needs you** — pick which jobs to score")
        with right:
            if st.button("Choose jobs to score ▶", type="primary",
                         key="rs_choose", use_container_width=True):
                _navigate("Workflow Detail", detail_workflow_id=wf_id)
    elif status in _DONE:
        rm = (st.session_state.get("last_response") or {}).get("run_metrics") or {}
        tail = f"  ·  ${rm['estimated_cost_usd']:.4f}" if rm.get("estimated_cost_usd") else ""
        left.markdown(f"🟢 **Last search done** — your matches are below{tail}")
        with right:
            r, n = st.columns(2)
            if r.button("Report", key="rs_report", use_container_width=True):
                _navigate("Run Report", workflow_id=wf_id, last_status=status)
            with n:
                _new_search_button("rs_new_done")
    elif status == "failed":
        left.markdown("🔴 **Last search failed**")
        with right:
            w, n = st.columns(2)
            if w.button("What happened", key="rs_what", use_container_width=True):
                _navigate("Live Run Monitor")
            with n:
                _new_search_button("rs_new_fail")
    else:
        left.caption(f"Search status: `{status or 'unknown'}`")
        with right:
            _new_search_button("rs_new_other")
    st.divider()


# ── Sidebar chip ──────────────────────────────────────────────────────────────

def _chip(wf_id: str | None, status: str | None) -> None:
    if not wf_id:
        return
    st.markdown("---")
    st.caption(f"{_CHIP_ICON.get(status, '⚪')} {_CHIP_LABEL.get(status, status or '—')}")
    if status == "running":
        if st.button("Watch live ▶", key="rs_chip_live", use_container_width=True):
            _navigate("Live Run Monitor")
    else:
        if st.button("Open Matches ▶", key="rs_chip_matches", use_container_width=True):
            _navigate("Matches")
