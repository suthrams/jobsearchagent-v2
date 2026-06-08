"""Per-job tailoring panel (ADR-090) - the reusable tailoring orchestration for one
``(workflow_id, job_id)``: generate a draft -> drafts picker -> approve/revise/reject/
edit decisions -> ADR-072 live chat + export.

Extracted so the **Opportunity page** and the **job-focused Resume Clinic** share one
implementation (the spec's "reuse the tailoring engine, don't rebuild it"). It wraps
the existing endpoints (`trigger_tailoring`, `submit_tailoring_decision`,
`open_tailoring_chat_session`) and the existing render components (the tailoring card,
the resume chat panel) - it adds no new agent or endpoint.
"""
from __future__ import annotations

import httpx
import streamlit as st

import app.ui.api_client as api
from app.ui.components.resume_chat_panel import render_chat_panel
from app.ui.components.tailoring import _render_tailoring_card
from app.ui.data import _cached_list_tailorings


def render_job_tailoring(wf_id: str, job_id: str, *, resume_profile: dict | None = None,
                         key_prefix: str, trigger_label: str = "✨ Tailor my resume",
                         on_demand_note: bool = False) -> None:
    """Render the tailoring trigger + drafts + decisions + live chat for one job."""
    if st.button(trigger_label, type="primary", key=f"{key_prefix}_tailor",
                 use_container_width=True):
        _run_tailoring(wf_id, job_id)
    if on_demand_note:
        st.caption(
            "⚠ This job was not auto-selected for deep review, so tailoring it runs a "
            "deep-review pass first (extra cost)."
        )
    _render_drafts(wf_id, job_id, resume_profile, key_prefix)


def _run_tailoring(wf_id: str, job_id: str) -> None:
    with st.spinner("Tailoring + fidelity review (~60-90s, longer if deep-reviewing first)…"):
        try:
            api.trigger_tailoring(wf_id, job_id)
        except httpx.ReadTimeout:
            # The synchronous server path can outlast the socket timeout; the draft
            # usually lands anyway (same contract as the old Workflow Detail button).
            _cached_list_tailorings.clear()
            st.warning("Client timed out, but the server may have finished. "
                       "Reload to see the new draft.")
            return
        except Exception as exc:  # noqa: BLE001
            st.error(f"Tailoring failed: {exc}")
            return
    _cached_list_tailorings.clear()
    st.rerun()


def _render_drafts(wf_id: str, job_id: str, resume_profile: dict | None,
                   key_prefix: str) -> None:
    try:
        with st.spinner("Loading tailored drafts…"):
            all_tailorings = _cached_list_tailorings(wf_id)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not load drafts: {exc}")
        return

    drafts = [t for t in all_tailorings if (t.get("job_id") or "") == job_id]
    if not drafts:
        st.caption("No tailored drafts yet. Click the button above to create one.")
        return

    drafts.sort(key=lambda t: t.get("created_at") or "", reverse=True)

    def _label(t: dict) -> str:
        tid = (t.get("tailoring_id") or t.get("id") or "")[:8]
        dec = t.get("decision")
        return f"Draft `{tid}…`{f' · {dec}' if dec else ''}  ·  {t.get('created_at', '')}"

    st.markdown(f"**Tailored drafts** ({len(drafts)}, newest first)")
    idx = 0
    if len(drafts) > 1:
        idx = st.selectbox(
            "Draft", range(len(drafts)), format_func=lambda i: _label(drafts[i]),
            key=f"{key_prefix}_draft_pick",
        )
    chosen = drafts[idx]

    def _decide(tid: str, choice: str, edited: dict | None = None) -> None:
        try:
            api.submit_tailoring_decision(tid, choice, edited=edited)
            _cached_list_tailorings.clear()
            st.toast(f"Decision saved: {choice}")
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Decision failed: {exc}")

    _render_tailoring_card(chosen, _decide, resume_profile=resume_profile)

    # ADR-072 live chat. The card may contain expanders, so the open-button and the
    # panel both live OUTSIDE any expander (here, at top level).
    ctid = chosen.get("tailoring_id") or chosen.get("id") or ""
    if ctid and st.button("💬 Open live chat", key=f"{key_prefix}_chat_open_{ctid}",
                          help="Refine this draft in live chat, then export it (ADR-072)."):
        try:
            with st.spinner("Opening chat session…"):
                sess = api.open_tailoring_chat_session(ctid)
            st.session_state["tail_chat_active_tid"] = ctid
            st.session_state[f"tail_chat_review_{ctid}"] = sess
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not open live chat: {exc}")

    render_live_chat_panel()


def render_live_chat_panel() -> None:
    """Render the active ADR-072 live-chat session, if one is open. Lives outside any
    expander (the tailoring card uses its own)."""
    active_tid = st.session_state.get("tail_chat_active_tid")
    key = f"tail_chat_review_{active_tid}" if active_tid else None
    if not (active_tid and key and st.session_state.get(key)):
        return
    st.markdown("---")
    st.subheader("💬 Live chat — refine & export the tailored resume")
    st.caption(f"Refining draft `{active_tid[:8]}…`. Chat to enhance the resume inline, "
               "then export. Close to return to the drafts.")
    if st.button("Close live chat", key="tp_chat_close"):
        st.session_state["tail_chat_active_tid"] = None
        st.rerun()
    render_chat_panel(
        st.session_state[key],
        user_id=st.session_state.current_user_id,
        state_key=key,
    )
