"""Cross-run analytics views: Top Matches, the three career-track tables, and
Top Target Companies.

Phase 3 of the UI refactor (docs/architecture/ui_refactor_plan.md). These five
nav entries are small and share the same scored-jobs source, so they live in one
module. Each is a render(ctx) using the sidebar filters carried on ctx
(min_score / search / include_excluded) and the active profile from session_state.
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from app.ui.components.tracks import render_track_table
from app.ui.data import _cached_scored_jobs, _get_config_cached
from app.ui.nav import ViewContext

_VALID_TRACKS = ["ic", "architect", "management"]


def _scored(ctx: ViewContext) -> pd.DataFrame:
    """Scored jobs for the active profile via the API (ADR-075 Phase 3)."""
    page = _cached_scored_jobs(st.session_state.current_user_id, ctx.include_excluded)
    return pd.DataFrame(page.get("items") or [])


def _active_tracks() -> list[str]:
    """The current profile's active scoring tracks (ADR-071), default all three."""
    cfg = _get_config_cached() or {}
    eff = cfg.get("effective_config", {}) or {}
    raw = (eff.get("scoring") or {}).get("tracks")
    if not isinstance(raw, list):
        return list(_VALID_TRACKS)
    chosen = [t for t in _VALID_TRACKS if t in raw]
    return chosen or list(_VALID_TRACKS)


def _inactive_track_notice(track: str, label: str) -> bool:
    """If `track` is not active for this profile, show a note and return True."""
    if track not in _active_tracks():
        st.info(
            f"The {label} track is not active for this profile, so no jobs are "
            f"scored on it. Enable it under **Settings -> Scoring** if you want it."
        )
        return True
    return False


def render_top_matches(ctx: ViewContext) -> None:
    st.header("Top Matches (across all runs)")
    df = _scored(ctx)
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
    if _inactive_track_notice("ic", "IC"):
        return
    df = _scored(ctx)
    if df.empty:
        st.info("No scored jobs yet. Kick off a run from **Start New Run** in the sidebar — "
                "jobs scored on the IC track will appear here.")
        st.stop()
    render_track_table(df, "technical_score", ctx.min_score)


def render_architect_track(ctx: ViewContext) -> None:
    st.header("Architect Track")
    if _inactive_track_notice("architect", "Architect"):
        return
    df = _scored(ctx)
    if df.empty:
        st.info("No scored jobs yet. Kick off a run from **Start New Run** in the sidebar — "
                "jobs scored on the Architect track will appear here.")
        st.stop()
    render_track_table(df, "architecture_score", ctx.min_score)


def render_management_track(ctx: ViewContext) -> None:
    st.header("Management Track")
    if _inactive_track_notice("management", "Management"):
        return
    df = _scored(ctx)
    if df.empty:
        st.info("No scored jobs yet. Kick off a run from **Start New Run** in the sidebar — "
                "jobs scored on the Management track will appear here.")
        st.stop()
    render_track_table(df, "leadership_score", ctx.min_score)


def render_companies(ctx: ViewContext) -> None:
    st.header("Top Target Companies")
    df = _scored(ctx)
    if df.empty:
        st.info("No scored jobs yet. Kick off a run from **Start New Run** in the sidebar — "
                "this view aggregates the best score per company across all runs.")
        st.stop()
    # ADR-071: only aggregate the track columns active for this profile.
    active = _active_tracks()
    _track_agg = {
        "ic": ("best_technical", ("technical_score", "max"), "Tech"),
        "architect": ("best_arch", ("architecture_score", "max"), "Arch"),
        "management": ("best_lead", ("leadership_score", "max"), "Lead"),
    }
    agg_spec = {"jobs": ("job_id", "count"), "best_overall": ("overall_score", "max")}
    rename_map = {"company": "Company", "jobs": "Roles", "best_overall": "Best"}
    for t in active:
        col, spec, short = _track_agg[t]
        agg_spec[col] = spec
        rename_map[col] = short
    agg = (
        df.groupby("company")
        .agg(**agg_spec)
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
        agg.rename(columns=rename_map),
        hide_index=True, use_container_width=True,
    )
