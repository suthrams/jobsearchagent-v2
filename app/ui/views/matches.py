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

Phase: ADR-088 merge (Tier 1) + "Open opportunity" routes to the Opportunity page
(Tier 2).
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import app.ui.api_client as api
from app.services.posting_age_filter import is_stale
from app.ui.components.favorites import favorited_ids, render_favorite_toggle
from app.ui.components.posting_link import source_badge
from app.ui.components.run_status import render_run_status
from app.ui.data import _cached_scored_jobs, _cached_user_resumes, _get_config_cached
from app.ui.formatting import format_posting_age_short
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


def _focus_jobs(df: pd.DataFrame, active_tracks: list[str], limit: int = 3) -> pd.DataFrame:
    """ADR-093 #2: the top `limit` jobs to ACT on now, ranked by best ACTIVE-track
    score (ADR-071), tie-broken by posting freshness then overall score. Pure +
    deterministic (no LLM) - it reorders data the scorer already produced. A
    recommendation of where to spend effort, not a status/tracker.
    """
    if df is None or df.empty:
        return df.head(0) if df is not None else pd.DataFrame()
    work = df.copy()
    score_cols = [_TRACK_SCORE[t] for t in active_tracks
                  if _TRACK_SCORE.get(t) in work.columns]
    if score_cols:
        work["_best"] = work[score_cols].apply(pd.to_numeric, errors="coerce").max(axis=1)
    elif "overall_score" in work.columns:
        work["_best"] = pd.to_numeric(work["overall_score"], errors="coerce")
    else:
        return work.head(0)
    work = work[work["_best"].notna() & (work["_best"] > 0)]
    if work.empty:
        return work
    # ISO date strings sort correctly lexicographically; missing -> "" sorts last.
    work["_posted"] = (work["posted_at"].fillna("").astype(str)
                       if "posted_at" in work.columns else "")
    work["_overall"] = pd.to_numeric(work.get("overall_score"), errors="coerce").fillna(0)
    work = work.sort_values(["_best", "_posted", "_overall"],
                            ascending=[False, False, False])
    return work.head(limit)


def _focus_card(job: pd.Series) -> None:
    title = str(job.get("title") or "(untitled)")
    company = str(job.get("company") or "—")
    loc = str(job.get("location") or "").strip()
    best = int(job["_best"]) if pd.notna(job.get("_best")) else 0
    st.markdown(f"**{title}**")
    st.caption(company + (f"  ·  {loc}" if loc else ""))
    st.progress(min(best, 100) / 100.0, text=f"Best fit {best}")
    # At-a-glance link reliability (ADR-093 #1): source + freshness, so the user
    # knows before clicking through whether the link is likely live.
    bits: list[str] = []
    badge = source_badge(job.get("source"))
    if badge:
        bits.append(badge)
    age = format_posting_age_short(job.get("posted_at"))
    if age:
        bits.append(age + (" ⚠️" if is_stale(job.get("posted_at")) else ""))
    if bits:
        st.caption("  ·  ".join(bits))
    why = (str(job.get("match_summary") or "")).strip()
    if why:
        st.caption(why[:140] + ("…" if len(why) > 140 else ""))
    rec = (str(job.get("recommended_next_action") or "")).strip()
    if rec:
        st.markdown(f"➡ _{rec[:120]}{'…' if len(rec) > 120 else ''}_")
    jid = str(job.get("job_id"))
    if st.button("Open ▶", key=f"focus_open_{jid}", type="primary",
                 use_container_width=True):
        _navigate("Opportunity",
                  detail_workflow_id=job.get("workflow_id"),
                  detail_job_id=job.get("job_id"))


def _render_focus(df: pd.DataFrame, active_tracks: list[str]) -> None:
    """The 'Where to focus' triage strip (ADR-093 #2): up to 3 cards of the
    strongest fits to act on now, each a one-click jump into its Opportunity page
    (tailor / prep). Suggestion only - no Apply/Save/status (CLAUDE.md guardrail)."""
    top = _focus_jobs(df, active_tracks, limit=3)
    if top is None or top.empty:
        return
    st.markdown("#### Where to focus")
    st.caption("Your strongest fits to act on now — open one to tailor your resume or "
               "prep for it. A suggestion of where to spend effort, not a checklist.")
    for col, (_, job) in zip(st.columns(len(top)), top.iterrows()):
        with col, st.container(border=True):
            _focus_card(job)
    st.markdown("---")


def _filters(ctx: ViewContext) -> ViewContext:
    """Render the cross-run filter controls in-screen and return them as a
    ViewContext (ADR-088 Phase 3 - contextual filters).

    These used to be always-on sidebar widgets that acted only here; they now live
    on the one screen that consumes them. Widget keys (``m_*``) are screen-local and
    get cleaned up on navigation away; the chosen values are mirrored onto the
    persistent ``flt_*`` session keys, which survive navigation (the entrypoint
    seeds them and New search reads ``flt_min_score`` for its threshold default).
    The incoming ``ctx`` is ignored - this screen owns its filters.
    """
    c1, c2, c3 = st.columns([2, 3, 2])
    min_score = c1.slider(
        "Minimum match score", 0, 100,
        value=int(st.session_state.get("flt_min_score", 75)), step=5,
        key="m_min_score",
        help="Show roles whose selected-track score is at or above this value.",
    )
    search = c2.text_input(
        "Search title / company",
        value=str(st.session_state.get("flt_search", "") or ""),
        placeholder="e.g. Staff Engineer", key="m_search",
    )
    include_excluded = c3.checkbox(
        "Include excluded jobs",
        value=bool(st.session_state.get("flt_include_excluded", False)),
        key="m_include_excluded",
        help="ADR-057: jobs you've excluded are hidden by default. Tick to surface them.",
    )
    st.session_state.flt_min_score = min_score
    st.session_state.flt_search = search
    st.session_state.flt_include_excluded = include_excluded
    return ViewContext(min_score=min_score, search=search, include_excluded=include_excluded)


def render(ctx: ViewContext) -> None:
    st.header("Matches")
    st.caption("Your best opportunities across every search, newest scores first.")

    # The live run-status strip (ADR-089): start/watch/results happen here, so the
    # core loop never leaves Matches. Auto-refreshes while a search is running.
    render_run_status("matches")

    # Contextual filters live here now, not in the global sidebar (ADR-088 Phase 3).
    eff = _filters(ctx)

    df = _scored(eff)
    if df.empty:
        _empty_state()
        return

    # ADR-093 #2: the "where to focus" triage strip - the strongest fits to act on
    # now, above the full browsable tabs. Ranked over the active tracks (ADR-071),
    # independent of the search box below.
    _render_focus(df, _active_tracks())

    roles_tab, companies_tab = st.tabs(["Roles", "Companies"])
    with roles_tab:
        _render_roles(eff, df)
    with companies_tab:
        _render_companies(eff, df)


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
    # NEW badge on rows scored by the latest finished run (ADR-089): the payoff of a
    # just-completed search is obvious without leaving Matches.
    _latest = (st.session_state.get("workflow_id")
               if st.session_state.get("last_status") in ("completed", "completed_with_errors")
               else None)
    if _latest and "workflow_id" in shown.columns:
        shown = shown.copy()
        shown["New"] = shown["workflow_id"].apply(
            lambda w: "🆕" if str(w) == str(_latest) else "")
        if shown["New"].str.len().sum():
            cols.insert(0, "New")
    # ADR-090: a ★ marker on already-favorited rows so the set is visible at a glance.
    _fav_ids = favorited_ids(st.session_state.current_user_id)
    if _fav_ids and "job_id" in shown.columns:
        shown = shown.copy()
        shown["★"] = shown["job_id"].apply(lambda j: "★" if str(j) in _fav_ids else "")
        if shown["★"].str.len().sum():
            cols.insert(0, "★")
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
            "★": st.column_config.TextColumn("★", width="small",
                                             help="In My favorite jobs"),
            "New": st.column_config.TextColumn("New", width="small",
                                               help="Scored by your most recent search"),
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
    b1, b2, b3, _ = st.columns([1, 1, 1, 2])
    if b1.button("Open opportunity", type="primary", key="matches_open"):
        _navigate(
            "Opportunity",
            detail_workflow_id=job.get("workflow_id"),
            detail_job_id=job.get("job_id"),
        )
    with b2:
        # ADR-090: flag this job for the Resume Clinic (a tailoring target, not a status).
        render_favorite_toggle(
            job_id=str(job.get("job_id")), workflow_id=str(job.get("workflow_id")),
            key="matches_favorite",
        )
    if b3.button("Exclude", key="matches_exclude"):
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
