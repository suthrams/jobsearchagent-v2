"""v2 Streamlit UI — extends v1 dashboard.py with Active Run HITL controls.

Browse views (Top Matches, IC/Architect/Mgmt Track, Companies, Run History) read
data/v2.db directly via db_reader.py. Control actions (start workflow, HITL
decisions, report fetch) call FastAPI via api_client.py.

Never import from app/workflows/ or app/agents/ — those are server-side only.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

load_dotenv()  # load .env so API_BASE_URL overrides and any future env vars are available

import app.ui.api_client as api
from app.ui.db_reader import (
    load_deep_review_results,
    load_interview_prep,
    load_scored_jobs,
    load_workflow_runs,
)

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Job Search Agent v2",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session state keys ────────────────────────────────────────────────────────
# workflow_id   : str | None  — set after POST /workflows
# last_status   : str | None  — last-known status from GET /workflows/{id}
# last_response : dict | None — full response payload

if "workflow_id" not in st.session_state:
    st.session_state.workflow_id = None
if "last_status" not in st.session_state:
    st.session_state.last_status = None
if "last_response" not in st.session_state:
    st.session_state.last_response = None


# ── Shared helpers ────────────────────────────────────────────────────────────

def score_badge(score: int | None) -> str:
    if score is None:
        return "—"
    if score >= 80:
        return f"🟢 {score}"
    if score >= 65:
        return f"🟡 {score}"
    if score >= 50:
        return f"🟠 {score}"
    return f"🔴 {score}"


def render_score_bars(job: dict) -> None:
    cols = st.columns(4)
    dims = [
        ("Technical",     job.get("technical_score")),
        ("Architecture",  job.get("architecture_score")),
        ("Leadership",    job.get("leadership_score")),
        ("Domain",        job.get("domain_score")),
    ]
    for col, (label, val) in zip(cols, dims):
        col.metric(label, val if val is not None else "—")


def render_track_table(df: pd.DataFrame, score_col: str, min_score: int, table_key: str) -> None:
    filtered = df[df[score_col] >= min_score].sort_values(score_col, ascending=False).copy()
    display = filtered[
        ["job_id", "title", "company", "location", score_col,
         "match_summary", "recommended_next_action", "url"]
    ].rename(columns={
        score_col: "Score",
        "match_summary": "Summary",
        "recommended_next_action": "Next Action",
        "url": "URL",
    }).reset_index(drop=True)

    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
        column_config={
            "job_id":      st.column_config.TextColumn("Job ID",     width="small"),
            "title":       st.column_config.TextColumn("Title",      width="large"),
            "company":     st.column_config.TextColumn("Company",    width="medium"),
            "location":    st.column_config.TextColumn("Location",   width="medium"),
            "Score":       st.column_config.ProgressColumn("Score",  min_value=0, max_value=100, format="%d"),
            "Summary":     st.column_config.TextColumn("Summary",    width="large"),
            "Next Action": st.column_config.TextColumn("Next Action",width="medium"),
            "URL":         st.column_config.LinkColumn("Link",       width="small"),
        },
    )
    st.caption(f"{len(filtered)} jobs with score >= {min_score}")


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("Job Search Agent v2")
    st.markdown("---")
    st.markdown("**Active Run**")
    view = st.radio(
        "View",
        [
            "Start New Run",
            "Monitor / HITL",
            "Run Report",
            "─── Browse Results ───",
            "Top Matches",
            "IC Track",
            "Architect Track",
            "Management Track",
            "Deep Review Results",
            "Interview Prep",
            "─── Analytics ───",
            "Companies",
            "Run History",
        ],
        index=0,
    )
    st.markdown("---")
    min_score = st.slider("Minimum score", 0, 100, 60, 5)
    st.markdown("---")
    search = st.text_input("Search title / company", placeholder="e.g. Staff Engineer")
    st.markdown("---")
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()

# ── Helper: skip separator items ──────────────────────────────────────────────
if view.startswith("───"):
    st.info("Select a view from the sidebar.")
    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# ACTIVE RUN SCREENS
# ══════════════════════════════════════════════════════════════════════════════

if view == "Start New Run":
    st.header("Start New Run")

    with st.form("start_run"):
        resume_id = st.text_input("Resume ID", value="res-001")
        roles = st.text_input("Roles (comma-separated)", value="Staff Engineer, Principal Engineer")
        locations = st.text_input("Locations (comma-separated)", value="Remote")
        track = st.radio("Career track", ["ic", "architect", "management"], horizontal=True)
        submitted = st.form_submit_button("Start Workflow")

    if submitted:
        search_criteria = {
            "roles": [r.strip() for r in roles.split(",") if r.strip()],
            "locations": [l.strip() for l in locations.split(",") if l.strip()],
        }
        effective_config = {"scoring": {"career_track": track}}
        try:
            resp = api.start_workflow(resume_id, search_criteria, effective_config=effective_config)
            st.session_state.workflow_id = resp["workflow_id"]
            st.session_state.last_status = "running"
            st.session_state.last_response = resp
            st.success(f"Workflow started: `{resp['workflow_id']}`")
            st.info("Switch to **Monitor / HITL** to track progress.")
        except Exception as e:
            st.error(f"Failed to start workflow: {e}")


elif view == "Monitor / HITL":
    st.header("Monitor / HITL Controls")

    wf_id = st.session_state.workflow_id
    if not wf_id:
        st.warning("No active workflow. Start one from **Start New Run**.")
        st.stop()

    st.caption(f"Workflow: `{wf_id}`")

    col_refresh, col_clear = st.columns([1, 4])
    if col_refresh.button("Refresh"):
        try:
            resp = api.get_workflow_status(wf_id)
            st.session_state.last_response = resp
            st.session_state.last_status = resp.get("status")
        except Exception as e:
            st.error(f"Could not fetch status: {e}")

    status = st.session_state.last_status or "unknown"
    resp = st.session_state.last_response or {}

    # ── Status display ────────────────────────────────────────────────────────
    status_color = {
        "running": "🔵", "waiting_for_user": "🟡",
        "completed": "🟢", "failed": "🔴",
    }.get(status, "⚪")
    st.markdown(f"**Status:** {status_color} `{status}`")

    step = resp.get("current_step")
    if step:
        st.markdown(f"**Step:** `{step}`")

    metrics = resp.get("run_metrics") or {}
    if metrics:
        m1, m2, m3 = st.columns(3)
        llm = metrics.get("llm_calls", 0)
        m1.metric("LLM calls", f"{llm} / 50")
        m2.metric("Est. cost", f"${metrics.get('estimated_cost_usd', 0):.4f}")
        m3.metric("Errors", len(resp.get("errors") or []))

    errors = resp.get("errors") or []
    if errors:
        with st.expander(f"Errors ({len(errors)})"):
            for err in errors:
                st.json(err)

    # ── HITL #1 — Job Selection ───────────────────────────────────────────────
    pending = resp.get("pending_decision") or {}
    if status == "waiting_for_user" and pending.get("decision_type") == "select_jobs_for_deep_review":
        st.markdown("---")
        st.subheader("Select Jobs for Deep Review")
        st.caption(pending.get("message", "Select up to 3 jobs."))

        eligible = pending.get("eligible_jobs") or []
        if not eligible:
            st.info("No eligible scored jobs found.")
        else:
            selected_ids = []
            for job in eligible:
                col_check, col_info = st.columns([1, 10])
                checked = col_check.checkbox(
                    "", key=f"sel_{job['job_id']}", value=False
                )
                if checked:
                    selected_ids.append(job["job_id"])
                col_info.markdown(
                    f"**{job['title']}** @ {job['company']}  |  "
                    f"Overall: {score_badge(job.get('overall_score'))}"
                )

            if st.button("Submit Selection", disabled=len(selected_ids) == 0):
                if len(selected_ids) > 3:
                    st.error("Select at most 3 jobs.")
                else:
                    try:
                        api.submit_job_selection(wf_id, selected_ids)
                        st.session_state.last_status = "running"
                        st.success("Selection submitted — workflow resuming.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Submission failed: {e}")

    # ── HITL #2 — Tailoring Approval ─────────────────────────────────────────
    elif status == "waiting_for_user" and pending.get("decision_type") == "approve_tailoring":
        st.markdown("---")
        st.subheader("Tailoring Approval")
        st.caption(pending.get("message", "Review the tailored resume draft."))

        fidelity_status = pending.get("fidelity_status", "unknown")
        approval_rec = pending.get("approval_recommendation", "unknown")
        f1, f2 = st.columns(2)
        f1.metric("Fidelity Status", fidelity_status)
        f2.metric("Recommendation", approval_rec)

        tailored = pending.get("tailored_resume") or {}
        if tailored:
            with st.expander("View tailored draft"):
                st.json(tailored)

        col_a, col_r, col_rej = st.columns(3)
        if col_a.button("Approve"):
            try:
                api.submit_tailoring_approval(wf_id, "approve")
                st.session_state.last_status = "running"
                st.success("Approved — workflow resuming.")
                st.rerun()
            except Exception as e:
                st.error(f"Failed: {e}")
        if col_r.button("Request Revision"):
            try:
                api.submit_tailoring_approval(wf_id, "revise")
                st.session_state.last_status = "running"
                st.success("Revision requested — workflow resuming.")
                st.rerun()
            except Exception as e:
                st.error(f"Failed: {e}")
        if col_rej.button("Reject"):
            try:
                api.submit_tailoring_approval(wf_id, "reject")
                st.session_state.last_status = "running"
                st.success("Rejected — workflow resuming to report.")
                st.rerun()
            except Exception as e:
                st.error(f"Failed: {e}")

    elif status == "completed":
        st.success("Workflow complete. View the report in **Run Report**.")

    elif status == "failed":
        st.error("Workflow failed. Check errors above.")


elif view == "Run Report":
    st.header("Run Report")

    wf_id = st.session_state.workflow_id
    if not wf_id:
        st.warning("No active workflow.")
        st.stop()

    status = st.session_state.last_status
    if status != "completed":
        st.info(f"Workflow status is `{status or 'unknown'}` — report is available when completed.")
        st.stop()

    try:
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
    except Exception as e:
        st.error(f"Could not fetch report: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# BROWSE VIEWS — read data/v2.db directly via db_reader.py
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Top Matches":
    st.header("Top Matches")
    df = load_scored_jobs()
    if df.empty:
        st.warning("No scored jobs found. Complete a workflow run first.")
        st.stop()

    if search:
        mask = (
            df["title"].str.contains(search, case=False, na=False)
            | df["company"].str.contains(search, case=False, na=False)
        )
        df = df[mask]

    filtered = df[df["overall_score"] >= min_score].copy()
    m1, m2, m3 = st.columns(3)
    m1.metric("Total scored", len(df))
    m2.metric(f"Score >= {min_score}", len(filtered))
    m3.metric("Companies", filtered["company"].nunique())

    render_track_table(filtered, "overall_score", min_score, "top_matches_table")


elif view == "IC Track":
    st.header("IC Engineering Track")
    df = load_scored_jobs()
    if df.empty:
        st.warning("No scored jobs found.")
        st.stop()
    render_track_table(df, "technical_score", min_score, "ic_table")


elif view == "Architect Track":
    st.header("Architect Track")
    df = load_scored_jobs()
    if df.empty:
        st.warning("No scored jobs found.")
        st.stop()
    render_track_table(df, "architecture_score", min_score, "arch_table")


elif view == "Management Track":
    st.header("Management Track")
    df = load_scored_jobs()
    if df.empty:
        st.warning("No scored jobs found.")
        st.stop()
    render_track_table(df, "leadership_score", min_score, "mgmt_table")


elif view == "Deep Review Results":
    st.header("Deep Review Results")

    wf_id = st.session_state.workflow_id
    if not wf_id:
        st.info("Enter a workflow ID to browse deep review results.")
        wf_id = st.text_input("Workflow ID")
        if not wf_id:
            st.stop()

    df = load_deep_review_results(wf_id)
    if df.empty:
        st.info("No deep review results found for this workflow.")
        st.stop()

    for _, row in df.iterrows():
        with st.expander(f"Job: {row['job_id']}"):
            st.markdown(f"**Fit Summary:** {row.get('overall_fit_summary', '—')}")
            st.markdown(f"**Positioning:** {row.get('positioning_summary', '—')}")
            st.markdown(f"**Recommended Action:** {row.get('recommended_next_action', '—')}")

            col_a, col_b = st.columns(2)
            col_a.markdown("**Resume Gaps** *(can tailor)*")
            try:
                gaps = json.loads(row.get("resume_gaps_json") or "[]")
                for g in gaps:
                    col_a.markdown(f"- {g}")
            except Exception:
                col_a.caption("n/a")

            col_b.markdown("**Career Gaps** *(must not fabricate)*")
            try:
                cgaps = json.loads(row.get("career_gaps_json") or "[]")
                for g in cgaps:
                    col_b.markdown(f"- {g}")
            except Exception:
                col_b.caption("n/a")


elif view == "Interview Prep":
    st.header("Interview Prep")

    wf_id = st.session_state.workflow_id
    if not wf_id:
        st.info("Enter a workflow ID to browse interview prep.")
        wf_id = st.text_input("Workflow ID")
        if not wf_id:
            st.stop()

    df = load_interview_prep(wf_id)
    if df.empty:
        st.info("No interview prep results found for this workflow.")
        st.stop()

    for _, row in df.iterrows():
        with st.expander(f"Job: {row['job_id']}"):
            try:
                topics = json.loads(row.get("likely_topics_json") or "[]")
                st.markdown("**Likely Topics:** " + ", ".join(topics))
            except Exception:
                pass
            try:
                plan = json.loads(row.get("seven_day_plan_json") or "[]")
                st.markdown("**7-Day Prep Plan:**")
                for item in plan:
                    st.markdown(f"- {item}")
            except Exception:
                pass
            try:
                weak = json.loads(row.get("weak_areas_json") or "[]")
                if weak:
                    st.markdown("**Areas to Defend:** " + ", ".join(weak))
            except Exception:
                pass


# ── Analytics ─────────────────────────────────────────────────────────────────

elif view == "Companies":
    st.header("Top Target Companies")
    df = load_scored_jobs()
    if df.empty:
        st.warning("No scored jobs found.")
        st.stop()

    agg = (
        df.groupby("company")
        .agg(
            jobs=("job_id", "count"),
            best_overall=("overall_score", "max"),
            best_technical=("technical_score", "max"),
            best_arch=("architecture_score", "max"),
            best_lead=("leadership_score", "max"),
        )
        .reset_index()
        .sort_values("best_overall", ascending=False)
    )
    agg = agg[agg["best_overall"] >= min_score]

    top = agg.head(20).sort_values("best_overall")
    fig = px.bar(
        top, x="best_overall", y="company", orientation="h",
        color="best_overall", color_continuous_scale="teal",
        labels={"best_overall": "Best Score", "company": "Company"},
        title=f"Top {len(top)} Companies by Best Match Score",
        text="best_overall",
    )
    fig.update_layout(showlegend=False, coloraxis_showscale=False, height=500)
    fig.update_traces(textposition="outside")
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        agg.rename(columns={
            "company": "Company", "jobs": "Roles",
            "best_overall": "Best", "best_technical": "Tech",
            "best_arch": "Arch", "best_lead": "Lead",
        }),
        hide_index=True, use_container_width=True,
    )


elif view == "Run History":
    st.header("Run History")
    df = load_workflow_runs()
    if df.empty:
        st.info("No workflow runs recorded yet.")
        st.stop()

    m1, m2 = st.columns(2)
    m1.metric("Total Runs", len(df))
    total_cost = df.get("estimated_cost_usd", pd.Series(dtype=float)).sum()
    m2.metric("Total Est. Cost", f"${total_cost:.4f}")

    st.dataframe(df, hide_index=True, use_container_width=True)
