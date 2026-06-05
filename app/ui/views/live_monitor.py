"""Live Run Monitor view - activity feed for the active workflow.

Phase 3 of the UI refactor (docs/architecture/ui_refactor_plan.md). Extracted
verbatim into render(ctx); all st.* calls run inside render().
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import app.ui.api_client as api
from app.ui.data import (
    _cached_agent_events,
    _cached_llm_calls,
    _cached_recent_workflows,
    _cached_step_executions,
)
from app.ui.nav import ViewContext
from app.workflows.limits import MAX_LLM_CALLS_PER_RUN


def render(ctx: ViewContext) -> None:
    st.header("Live Run Monitor")

    wf_id = st.session_state.workflow_id
    if not wf_id:
        st.warning("No active workflow in this session.")
        recent = _cached_recent_workflows()
        if not recent.empty:
            st.markdown("**Reconnect to a recent workflow:**")
            for _, row in recent.iterrows():
                _wf = row["workflow_id"]
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"`{_wf}`  \n{int(row.get('jobs_scored', 0))} scored")
                if c2.button("Reconnect", key=f"rc_{_wf}"):
                    st.session_state.workflow_id = _wf
                    st.rerun()
        st.stop()

    st.caption(f"Workflow: `{wf_id}`")
    cols = st.columns([1, 1, 4])
    if cols[0].button("Refresh"):
        st.cache_data.clear()
        try:
            resp = api.get_workflow_status(wf_id)
            st.session_state.last_response = resp
            st.session_state.last_status = resp.get("status")
        except Exception as exc:
            st.error(f"Could not fetch status: {exc}")

    status = st.session_state.last_status or "unknown"
    resp = st.session_state.last_response or {}

    if status == "running" and cols[1].button("▶ Retry", help="Re-submit after server restart"):
        try:
            api.retry_workflow(wf_id)
            st.session_state.last_status = "running"
            st.success("Workflow re-submitted.")
            st.rerun()
        except Exception as exc:
            st.error(f"Retry failed: {exc}")

    # ADR-083: cooperative cancel. Takes effect at the next node boundary.
    if status in ("running", "cancelling") and cols[2].button(
        "⏹ Cancel", help="Stop this run at the next step boundary"
    ):
        try:
            api.cancel_workflow(wf_id)
            st.session_state.last_status = "cancelling"
            st.warning("Cancellation requested - the run will stop at the next step.")
            st.rerun()
        except Exception as exc:
            st.error(f"Cancel failed: {exc}")

    icon = {
        "running": "🔵", "completed": "🟢", "failed": "🔴",
        "cancelling": "🟠", "cancelled": "⚫",
    }.get(status, "⚪")
    st.markdown(f"**Status:** {icon} `{status}`")
    if resp.get("current_step"):
        st.markdown(f"**Step:** `{resp['current_step']}`")

    metrics = resp.get("run_metrics") or {}
    if metrics:
        m1, m2, m3 = st.columns(3)
        m1.metric("LLM calls", f"{metrics.get('llm_calls', 0)} / {MAX_LLM_CALLS_PER_RUN}")
        m2.metric("Est. cost", f"${metrics.get('estimated_cost_usd', 0):.4f}")
        m3.metric("Errors", len(resp.get("errors") or []))

    errors = resp.get("errors") or []
    if errors:
        with st.expander(f"Errors ({len(errors)})"):
            for err in errors:
                st.json(err)

    st.markdown("---")
    st.subheader("Run Activity")
    _STEP_ICON = {"completed": "✅", "failed": "❌", "started": "🔄"}

    steps_df = _cached_step_executions(wf_id)
    if steps_df.empty:
        st.caption("No steps recorded yet — workflow may still be initialising.")
    else:
        for _, row in steps_df.iterrows():
            ic = _STEP_ICON.get(row["status"], "⚪")
            dur = (
                f"  `{int(row['duration_ms']):,} ms`"
                if pd.notna(row.get("duration_ms")) and row["duration_ms"]
                else ""
            )
            notes = f"  — {row['notes']}" if row.get("notes") else ""
            st.markdown(f"{ic} **{row['step']}**{dur}{notes}")

    events_df = _cached_agent_events(wf_id)
    if not events_df.empty:
        with st.expander(f"Agent Events ({len(events_df)})", expanded=(status == "running")):
            display = events_df.copy()
            display[""] = display["event_type"].map(_STEP_ICON).fillna("⚪")
            display["Time"] = display["created_at"].str[11:19]
            display["Summary"] = display.apply(
                lambda r: (r.get("output_summary") or r.get("input_summary") or "")[:120],
                axis=1,
            )
            display["Duration"] = display["duration_ms"].apply(
                lambda x: f"{int(x):,} ms" if pd.notna(x) and x else "—"
            )
            st.dataframe(
                display[["", "Time", "agent_name", "event_type", "Duration", "Summary"]]
                .rename(columns={"agent_name": "Agent", "event_type": "Event"})
                .iloc[::-1],
                hide_index=True, use_container_width=True,
            )

    llm_df = _cached_llm_calls(wf_id)
    if not llm_df.empty:
        total_cost = llm_df["estimated_cost"].sum()
        total_tokens = int(llm_df["tokens_input"].sum() + llm_df["tokens_output"].sum())
        with st.expander(f"LLM Calls ({len(llm_df)}) · {total_tokens:,} tokens · ${total_cost:.4f}"):
            st.dataframe(
                llm_df[["agent_name", "model", "tokens_input", "tokens_output",
                        "estimated_cost", "latency_ms"]]
                .rename(columns={
                    "agent_name": "Agent", "model": "Model",
                    "tokens_input": "In", "tokens_output": "Out",
                    "estimated_cost": "Cost ($)", "latency_ms": "Latency (ms)",
                })
                .iloc[::-1],
                hide_index=True, use_container_width=True,
            )

    if status == "completed":
        st.success("Workflow complete — open it in **Workflow Detail** for a unified view.")
    elif status == "failed":
        st.error("Workflow failed — see errors above.")
    elif status == "cancelled":
        st.info("Workflow cancelled.")
    elif status == "cancelling":
        st.warning("Cancelling — the run will stop at the next step boundary.")
