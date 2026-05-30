"""Run Report view - the generated markdown report for the active workflow.

Phase 3 of the UI refactor (docs/architecture/ui_refactor_plan.md). Extracted
verbatim from streamlit_app.py's inline block into a render(ctx). All st.* calls
run inside render(); importing this module does nothing.
"""
from __future__ import annotations

import streamlit as st

import app.ui.api_client as api
from app.ui.nav import ViewContext


def render(ctx: ViewContext) -> None:
    st.header("Run Report")
    wf_id = st.session_state.workflow_id
    if not wf_id:
        st.warning("No active workflow.")
        st.stop()
    status = st.session_state.last_status
    if status not in ("completed", "completed_with_errors"):
        st.info(f"Report is available when the workflow completes. Current status: `{status or 'not started'}`.")
        st.stop()
    try:
        with st.spinner("Loading report…"):
            resp = api.get_report(wf_id)
        report = resp.get("report") or {}
        st.caption(f"Generated: {report.get('generated_at', '—')}")
        st.markdown(report.get("markdown", "_No report content._"))
        st.download_button(
            "Download Markdown",
            data=report.get("markdown", ""),
            file_name=f"report_{wf_id}.md",
            mime="text/markdown",
        )
    except Exception as exc:
        st.error(f"Could not fetch report: {exc}")
