"""Matches - the job-seeker home base (ADR-088).

Merges the former Top Matches + IC / Architect / Management track views + Top
Companies into one screen:

- a **Roles** tab: scored jobs across every run, sorted by a segment that shows
  only the profile's ACTIVE tracks (ADR-071) plus "Best fit" (overall score);
- a **Companies** tab: the best-score-per-company aggregation + chart.

Rows are **select-then-act** (``st.dataframe(on_select=...)`` -> action buttons),
because Streamlit cannot render an in-app link or button inside a table cell
(ADR-088 / UX-review R-2). The cross-run filters (min score / search /
include-excluded) arrive on ``ctx`` as today.

Phase: ADR-088 Tier-1 merge. "Open opportunity" routes to Job Detail until the
Tier-2 Opportunity page lands, at which point it is repointed there.
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import app.ui.api_client as api
from app.ui.data import _cached_scored_jobs, _cached_user_resumes, _get_config_cached
from app.ui.nav import ViewContext, _navigate

_VALID_TRACKS = ["ic", "architect", "management"]
_TRACK_LABEL = {"ic": "IC", "architect": "Architecture", "management": "Management"}
_TRACK_SCORE = {
    "ic": "technical_score",
    "architect": "architecture_score",
    "management": "leadership_score",
}
_LABEL_TO_SCORE = {_TRACK_LABEL[t]: _TRACK_SCORE[t] for t in _VALID_TRACKS}


def _scored(ctx: ViewContext) -> pd.DataFrame:
    """Scored jobs for the active profile via the API (ADR-075)."""
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


def _has_resume() -> bool:
    """True if the active profile has at least one stored resume (for the empty state)."""
    try:
        page = _cached_user_resumes(st.session_state.current_user_id) or {}
    except Exception:
        return True  # never block the user on a read hiccup
    items = page.get("resumes") or page.get("items") or []
    return bool(items)


def _empty_state() -> None:
    """Branched first-run state (UX-review R-6): no resume vs no runs."""
    if not _has_resume():
        st.subheader("No resume on this profile yet")
        st.caption("Scoring needs a resume to compare roles against.")
        if st.button("Add your resume", type="primary"):
            _navigate("Profiles")
    else:
        st.subheader("No searches yet")
        st.caption("Find roles scored against your resume across your tracks.")
        if st.button("Start your first search", type="primary"):
            _navigate("Start New Run")


def render(ctx: ViewContext) -> None:
    st.header("Matches")
    st.caption("Your best opportunities across every search, newest scores first.")

    df = _scored(ctx)
    if df.empty:
        _empty_state()
        return

    roles_tab, companies_tab = st.tabs(["Roles", "Companies"])
    with roles_tab:
        _render_roles(ctx, df)
    with companies_tab:
        _render_companies(ctx, df)


def _render_roles(ctx: ViewContext, df: pd.DataFrame) -> None:
    # Sort segment: "Best fit" (overall) + only the profile's active tracks (ADR-071).
    active = _active_tracks()
    options = ["Best fit"] + [_TRACK_LABEL[t] for t in active]
    choice = st.segmented_control(
        "Sort", options, default="Best fit", key="matches_sort",
    ) or "Best fit"
    score_col = _LABEL_TO_SCORE.get(choice, "overall_score")

    # Cross-run filters (carried on ctx, as today).
    work = df.copy()
    if ctx.search:
        mask = (
            work["title"].str.contains(ctx.search, case=False, na=False)
            | work["company"].str.contains(ctx.search, case=False, na=False)
        )
        work = work[mask]
    # A null track score (inactive/unscored) must not qualify - treat as -1 for sort
    # and exclude from the threshold.
    work[score_col] = pd.to_numeric(work.get(score_col), errors="coerce")
    shown = work[work[score_col] >= ctx.min_score].sort_values(
        score_col, ascending=False
    ).reset_index(drop=True)

    c1, c2, c3 = st.columns(3)
    c1.metric("Scored", len(df))
    c2.metric(f"{choice} >= {ctx.min_score}", len(shown))
    c3.metric("Companies", int(shown["company"].nunique()) if not shown.empty else 0)

    if shown.empty:
        st.info("No jobs clear the current filters. Lower the minimum score or clear the search.")
        return

    cols = ["title", "company", "location", score_col, "match_summary",
            "recommended_next_action", "url"]
    if "posted_at" in shown.columns:
        cols.insert(3, "posted_at")
    display = shown[[c for c in cols if c in shown.columns]].rename(columns={
        score_col: "Score",
        "match_summary": "Summary",
        "recommended_next_action": "Agent recommendation",
        "url": "URL",
        "posted_at": "Posted",
    })

    event = st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "title": st.column_config.TextColumn("Title", width="large"),
            "company": st.column_config.TextColumn("Company", width="medium"),
            "location": st.column_config.TextColumn("Location", width="small"),
            "Posted": st.column_config.TextColumn("Posted", width="small"),
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%d"),
            "Summary": st.column_config.TextColumn("Summary", width="large"),
            "Agent recommendation": st.column_config.TextColumn(
                "Agent recommendation", width="large"),
            "URL": st.column_config.LinkColumn("Link", width="small"),
        },
    )
    st.caption(f"{len(shown)} roles with {choice} score >= {ctx.min_score}. "
               "Select a row to act on it.")

    rows = event.selection.rows if event and event.selection else []
    if not rows:
        return
    job = shown.iloc[rows[0]]
    st.markdown(f"**Selected:** {job.get('title', '?')} - {job.get('company', '?')}")
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Open opportunity", type="primary", key="matches_open"):
        # Interim target until the Tier-2 Opportunity page lands (ADR-088).
        _navigate(
            "Job Detail",
            detail_workflow_id=job.get("workflow_id"),
            detail_job_id=job.get("job_id"),
        )
    if b2.button("Exclude", key="matches_exclude"):
        try:
            api.exclude_job(str(job.get("job_id")))
            st.cache_data.clear()
            st.success("Excluded. It will not show in future matches.")
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Exclude failed: {exc}")


def _render_companies(ctx: ViewContext, df: pd.DataFrame) -> None:
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
    if agg.empty:
        st.info("No companies clear the current minimum score.")
        return
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
        agg.rename(columns=rename_map), hide_index=True, use_container_width=True,
    )
