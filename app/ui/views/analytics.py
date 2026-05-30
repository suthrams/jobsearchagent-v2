"""Cross-run analytics views: Top Matches, the three career-track tables, and
Top Target Companies.

Phase 3 of the UI refactor (docs/architecture/ui_refactor_plan.md). These five
nav entries are small and share the same scored-jobs source, so they live in one
module. Each is a render(ctx) using the sidebar filters carried on ctx
(min_score / search / include_excluded) and the active profile from session_state.
"""
from __future__ import annotations

import plotly.express as px
import streamlit as st

from app.ui.components.tracks import render_track_table
from app.ui.db_reader import load_scored_jobs
from app.ui.nav import ViewContext


def render_top_matches(ctx: ViewContext) -> None:
    st.header("Top Matches (across all runs)")
    df = load_scored_jobs(include_excluded=ctx.include_excluded, user_id=st.session_state.current_user_id)
    if df.empty:
        st.info("No scored jobs yet. Kick off a run from **Start New Run** in the sidebar — "
                "results will populate this view automatically.")
        st.stop()
    if ctx.search:
        mask = (
            df["title"].str.contains(ctx.search, case=False, na=False)
            | df["company"].str.contains(ctx.search, case=False, na=False)
        )
        df = df[mask]
    filtered = df[df["overall_score"] >= ctx.min_score].copy()
    m1, m2, m3 = st.columns(3)
    m1.metric("Total scored", len(df))
    m2.metric(f"Score >= {ctx.min_score}", len(filtered))
    m3.metric("Companies", filtered["company"].nunique())
    render_track_table(filtered, "overall_score", ctx.min_score)


def render_ic_track(ctx: ViewContext) -> None:
    st.header("IC Engineering Track")
    df = load_scored_jobs(include_excluded=ctx.include_excluded, user_id=st.session_state.current_user_id)
    if df.empty:
        st.info("No scored jobs yet. Kick off a run from **Start New Run** in the sidebar — "
                "jobs scored on the IC track will appear here.")
        st.stop()
    render_track_table(df, "technical_score", ctx.min_score)


def render_architect_track(ctx: ViewContext) -> None:
    st.header("Architect Track")
    df = load_scored_jobs(include_excluded=ctx.include_excluded, user_id=st.session_state.current_user_id)
    if df.empty:
        st.info("No scored jobs yet. Kick off a run from **Start New Run** in the sidebar — "
                "jobs scored on the Architect track will appear here.")
        st.stop()
    render_track_table(df, "architecture_score", ctx.min_score)


def render_management_track(ctx: ViewContext) -> None:
    st.header("Management Track")
    df = load_scored_jobs(include_excluded=ctx.include_excluded, user_id=st.session_state.current_user_id)
    if df.empty:
        st.info("No scored jobs yet. Kick off a run from **Start New Run** in the sidebar — "
                "jobs scored on the Management track will appear here.")
        st.stop()
    render_track_table(df, "leadership_score", ctx.min_score)


def render_companies(ctx: ViewContext) -> None:
    st.header("Top Target Companies")
    df = load_scored_jobs(include_excluded=ctx.include_excluded, user_id=st.session_state.current_user_id)
    if df.empty:
        st.info("No scored jobs yet. Kick off a run from **Start New Run** in the sidebar — "
                "this view aggregates the best score per company across all runs.")
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
    agg = agg[agg["best_overall"] >= ctx.min_score]
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
