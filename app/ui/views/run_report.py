"""Run Report view - the generated markdown report for the active workflow.

Phase 3 of the UI refactor (docs/architecture/ui_refactor_plan.md). Extracted
verbatim from streamlit_app.py's inline block into a render(ctx). All st.* calls
run inside render(); importing this module does nothing.
"""
from __future__ import annotations

import streamlit as st

import app.ui.api_client as api
from app.ui.components.favorites import favorited_ids, render_favorite_toggle
from app.ui.data import _cached_favorites, _cached_workflow_jobs
from app.ui.nav import ViewContext, _navigate, back_button


def render(ctx: ViewContext) -> None:
    back_button("Workflow History")  # in-app Back to Searches (ADR-088 F)
    st.header("Run report")
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

    _flag_for_clinic(wf_id)


# ── Flag a job for the Resume Clinic (ADR-090 favorite -> clinic bridge) ───────

def _flag_for_clinic(wf_id: str) -> None:
    """Per-job affordance to take a job straight from the report into the Resume
    Clinic: ⭐ flag it (a favorite = the clinic's focus list), or jump straight to a
    clinic session focused on it. Closes the favorite->clinic bridge on this screen."""
    st.markdown("---")
    st.subheader("Tailor a job in the Resume Clinic")
    st.caption("Flag a job (⭐) to focus a Resume Clinic session on it, or open the "
               "clinic straight away to tailor your resume for that role.")

    jobs = _cached_workflow_jobs(wf_id)
    if jobs is None or jobs.empty:
        st.caption("No scored jobs in this run to flag.")
        return

    fav_ids = favorited_ids(st.session_state.current_user_id)
    for _, row in jobs.iterrows():
        job_id = str(row.get("job_id"))
        title = row.get("title") or "(untitled)"
        company = row.get("company") or "—"
        score = row.get("overall_score")
        c_info, c_fav, c_go = st.columns([4, 1.4, 2])
        with c_info:
            line = f"**{title}** · {company}"
            if score is not None:
                line += f"  ·  overall {int(score)}"
            st.markdown(line)
        with c_fav:
            render_favorite_toggle(job_id=job_id, workflow_id=wf_id,
                                   key=f"rr_fav_{job_id}")
        with c_go:
            if st.button("🩺 Analyze in clinic", key=f"rr_clinic_{job_id}",
                         use_container_width=True,
                         help="Favorite this job and open a Resume Clinic session focused on it."):
                _open_in_clinic(wf_id, job_id, job_id in fav_ids)


def _open_in_clinic(wf_id: str, job_id: str, already_fav: bool) -> None:
    """Favorite the job if needed, then navigate to the Resume Clinic with this job
    pre-focused (honored by resume_clinic via the clinic_focus_job_id hint)."""
    user_id = st.session_state.current_user_id
    if not already_fav:
        try:
            api.add_favorite(user_id, wf_id, job_id)
            _cached_favorites.clear()
        except api.FavoritesCapError:
            st.warning("You are at the favorite-jobs limit. Un-favorite one first, "
                       "then try again.")
            return
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not flag this job: {exc}")
            return
    _navigate("Resume Clinic", clinic_focus_job_id=job_id)
