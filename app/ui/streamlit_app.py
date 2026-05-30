"""v2 Streamlit UI.

Layout (sidebar order is intentional — top of the page first):

  * Workflow History (default landing) — list of all runs, click to drill in
  * Workflow Detail                    — per-run unified view: jobs, scores,
                                         deep review, advice, prep, settings
                                         used for that run, constraints hit
  * Start New Run                      — settings inline + custom URLs textarea
  * Live Run Monitor                   — activity feed for the currently running run
  * Run Report                         — generated markdown report
  * Settings                           — view + edit user-overridable config
  * (Cross-run analytics — Top Matches, IC/Architect/Mgmt Track, Companies)

Browse views read data/v2.db directly via db_reader.py.
Control actions (start workflow, edit config) call FastAPI via api_client.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure the project root is on sys.path so `import app.*` works when launched
# via `streamlit run app/ui/streamlit_app.py` (Streamlit puts the script's
# directory on sys.path[0], not the project root).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

import app.ui.api_client as api
import app.ui.nav as nav
from app.ui.nav import _navigate
# Pure formatting helpers extracted to app/ui/formatting.py (UI refactor Phase 1).
# Imported by bare name so existing call sites are unchanged. (score_badge and
# _tokenize also live in formatting.py for reuse by view modules; the entrypoint
# no longer references them directly, so they are not imported here.)
from app.ui.formatting import (
    _checked,
    _fmt_ts,
    _get_nested,
    _label_with_cost,
)
# Cached data-access wrappers extracted to app/ui/data.py (UI refactor Phase 2).
# (_load_yaml_config also lives in data.py but is only used there by
# _get_config_cached, so the entrypoint does not import it.)
from app.ui.data import (
    _cached_get_providers,
    _cached_list_tailorings,
    _cached_list_users,
    _get_config_cached,
)
# Shared render components extracted to app/ui/components/ (UI refactor Phase 2).
from app.ui.components.tailoring import _render_tailoring_card
# Migrated per-screen views + their dispatch registry (UI refactor Phase 3).
# (Components/db-reader helpers used only by migrated views moved with them.)
from app.ui.views import REGISTRY as VIEW_REGISTRY
from app.services.constraint_analyzer import analyze, summary_metrics
from app.services.cost_breakdown import compute_breakdown
from app.ui.db_reader import (
    load_deep_review_results,
    load_interview_prep,
    load_recent_workflows,
    load_user_clinic_reviews,
    load_user_resumes,
    load_workflow_jobs,
    load_workflow_run,
)


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Job Search Agent v2",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session state ─────────────────────────────────────────────────────────────

for _key, _default in (
    ("workflow_id", None),
    ("last_status", None),
    ("last_response", None),
    ("detail_workflow_id", None),
    ("detail_job_id", None),
    ("config_cache", None),
    ("sidebar_view", "Workflow History"),
    ("current_user_id", "0"),  # ADR-062: active profile; default = pre-existing data
    ("onboard_step", 1),       # onboarding wizard cursor
    ("onboard_new_user_id", None),
):
    if _key not in st.session_state:
        st.session_state[_key] = _default

# ADR-062: mirror the active profile onto the API client before any request fires
# this rerun. The sidebar selector may change it below, after which we re-set it.
api.set_user_id(st.session_state.current_user_id)


# Auto-reconnect to the most recent workflow on first load.
# Failures are stored on session_state so the sidebar can surface them as a caption
# instead of the user wondering why "Active Run" is empty.
if "workflow_reconnect_attempted" not in st.session_state:
    st.session_state.workflow_reconnect_attempted = True
    st.session_state.workflow_reconnect_error = None
    if st.session_state.workflow_id is None:
        try:
            _recent = load_recent_workflows()
            if not _recent.empty and "workflow_id" in _recent.columns:
                _reconnect_id = _recent.iloc[0]["workflow_id"]
                st.session_state.workflow_id = _reconnect_id
                try:
                    _reconnect_resp = api.get_workflow_status(_reconnect_id)
                    st.session_state.last_status = _reconnect_resp.get("status")
                    st.session_state.last_response = _reconnect_resp
                except Exception as exc:
                    st.session_state.workflow_reconnect_error = (
                        f"Could not fetch status for the most-recent run: {exc}"
                    )
        except Exception as exc:
            st.session_state.workflow_reconnect_error = (
                f"Could not load recent workflows from the database: {exc}"
            )


# ── Shared render helpers ─────────────────────────────────────────────────────

# ── Flush pending navigation before the radio widget is created ───────────────
# _navigate() cannot write sidebar_view after the widget is instantiated, so it
# stores the destination in _pending_nav and we apply it here on the next cycle.
if st.session_state.get("_pending_nav"):
    st.session_state.sidebar_view = st.session_state.pop("_pending_nav")

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("Job Search Agent v2")

    # ── Profile selector (ADR-062) ───────────────────────────────────────────
    # Picks whose search this is. Re-scopes every history / analytics read and
    # the resume picker, and tags new runs with this owner. No auth — this is a
    # cooperative selector, not an access boundary (ADR-062 Decision E).
    _users = _cached_list_users()
    if _users:
        _id_to_label = {str(u["id"]): f"{u['name']}  (#{u['id']})" for u in _users}
        _ids = list(_id_to_label.keys())
        _cur = st.session_state.current_user_id
        if _cur not in _ids:
            _cur = _ids[0]
        _chosen = st.selectbox(
            "Profile",
            _ids,
            index=_ids.index(_cur),
            format_func=lambda i: _id_to_label.get(i, i),
            key="_profile_select",
            help="Whose search this is. Switching re-scopes history, analytics, "
                 "and the resume picker to that profile.",
        )
        if _chosen != st.session_state.current_user_id:
            st.session_state.current_user_id = _chosen
            api.set_user_id(_chosen)
            st.cache_data.clear()
            st.session_state.config_cache = None
            st.rerun()
        _note = next((u.get("note") for u in _users if str(u["id"]) == _chosen), None)
        if _note:
            st.caption(_note)
    else:
        st.caption("No profiles found (backend offline?).")
    if st.button("＋ Add profile", use_container_width=True):
        st.session_state.onboard_step = 1
        st.session_state.onboard_new_user_id = None
        _navigate("Profiles")

    st.markdown("---")
    # View list + (later) dispatch live in app/ui/nav.py — the single source of
    # truth for the UI refactor (docs/architecture/ui_refactor_plan.md, Phase 0).
    view = st.radio(
        "View",
        nav.NAV_ITEMS,
        key="sidebar_view",
    )
    st.markdown("---")
    min_score = st.slider(
        "Minimum match score",
        min_value=0, max_value=100, value=75, step=5,
        help="Jobs with any track score (technical / architecture / leadership) at or above "
             "this value qualify for deep review and interview prep.",
    )
    st.markdown("---")
    search = st.text_input("Search title / company", placeholder="e.g. Staff Engineer")
    include_excluded = st.checkbox(
        "Include excluded jobs",
        value=False,
        help="ADR-057: jobs you've explicitly excluded are hidden from cross-run "
             "analytics by default. Tick to surface them.",
    )
    st.markdown("---")
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.session_state.config_cache = None
        st.rerun()
    if st.session_state.get("workflow_reconnect_error"):
        st.caption(f"⚠ {st.session_state.workflow_reconnect_error}")
    if st.session_state.workflow_id:
        _wst = st.session_state.last_status or "unknown"
        _wicon = {
            "running": "🔵", "waiting_for_user": "🟡",
            "completed": "🟢", "failed": "🔴",
        }.get(_wst, "⚪")
        st.markdown("---")
        st.markdown(f"**Active Run** {_wicon} `{_wst}`")
        st.caption(f"`{st.session_state.workflow_id[:12]}…`")
        _wresp = st.session_state.last_response or {}
        _wstep = _wresp.get("current_step")
        if _wstep:
            st.caption(f"Step: `{_wstep}`")
        _wm = _wresp.get("run_metrics") or {}
        if _wm.get("llm_calls"):
            st.caption(f"{_wm['llm_calls']} calls · ${_wm.get('estimated_cost_usd', 0):.4f}")
        _b1, _b2 = st.columns(2)
        if _b1.button("Detail", key="sb_open_detail", use_container_width=True):
            _navigate("Workflow Detail", detail_workflow_id=st.session_state.workflow_id)
        if _b2.button("Live", key="sb_open_live", use_container_width=True):
            _navigate("Live Run Monitor")

if view == nav.SEPARATOR:
    st.info("Select a view from the sidebar.")
    st.stop()

# ── Registry dispatch (UI refactor Phase 3) ───────────────────────────────────
# Views migrated into app/ui/views/ render here via REGISTRY[view](ctx); the
# legacy if/elif chain below handles the rest until they migrate too. ctx carries
# the sidebar filter widgets; workflow_id / current_user_id stay on session_state.
ctx = nav.ViewContext(min_score=min_score, search=search, include_excluded=include_excluded)
if view in VIEW_REGISTRY:
    VIEW_REGISTRY[view](ctx)
    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOW DETAIL — unified per-run drill-down
# ══════════════════════════════════════════════════════════════════════════════

if view == "Workflow Detail":
    st.header("Workflow Detail")

    # Sync the input widget to the navigation target on actual nav changes (row
    # click in History or sidebar button) but preserve user typing across reruns.
    # Why: st.text_input without a key holds onto its old widget value and ignores
    # the new value= arg, so a fresh detail_workflow_id from _navigate would be
    # clobbered back to whatever was in the input on the previous render.
    nav_target = st.session_state.detail_workflow_id or st.session_state.workflow_id or ""
    if nav_target and st.session_state.get("_detail_wf_synced") != nav_target:
        st.session_state.detail_wf_input = nav_target
        st.session_state._detail_wf_synced = nav_target

    wf_id = st.text_input("Workflow ID", key="detail_wf_input",
                          help="Pick a run from Workflow History or paste an ID.")
    if not wf_id:
        st.info("No workflow selected.")
        st.stop()
    st.session_state.detail_workflow_id = wf_id

    record = load_workflow_run(wf_id)
    state = (record or {}).get("state") or {}
    status = (record or {}).get("status", "unknown")

    # Status header
    icon = {"running": "🔵", "completed": "🟢", "failed": "🔴", "completed_with_errors": "🟠",
            "awaiting_scoring_selection": "🟡"}.get(status, "⚪")
    h1, h2 = st.columns([3, 1])
    h1.markdown(f"### {icon} `{status}`")
    h2.caption(f"Started: {(record or {}).get('started_at', '—')}")

    metrics = summary_metrics(state)
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Discovered", metrics["jobs_discovered"])
    g2.metric("Scored", metrics["jobs_scored"])
    g3.metric("Selected (auto)", metrics["jobs_selected"])
    g4.metric("LLM cost", f"${metrics['estimated_cost_usd']:.4f}")
    g5, g6, g7, g8 = st.columns(4)
    g5.metric("LLM calls", metrics["llm_calls"])
    g6.metric("Tokens in", f"{metrics['tokens_input']:,}")
    g7.metric("Tokens out", f"{metrics['tokens_output']:,}")
    g8.metric("Review rounds", metrics["review_rounds"])

    # ── Manual scoring selection (ADR-060) ────────────────────────────────────
    # When a manual-selection run is parked after discovery, let the user pick
    # which discovered jobs are worth the research + scoring spend. Only the
    # selected jobs are scored; the rest are skipped.
    if status == "awaiting_scoring_selection":
        st.markdown("---")
        st.subheader("🧭 Select jobs to score")
        st.caption(
            "Manual selection is on for this run. Discovery cast a wide net; pick "
            "the jobs worth the research + scoring spend. Only the jobs you select "
            "are scored -- the rest are skipped, at no cost."
        )
        discovered = state.get("normalized_jobs") or []
        if not discovered:
            st.info("No jobs were discovered for this run.")
        else:
            options: dict[str, str] = {}
            for j in discovered:
                jid = j.get("id") or j.get("source_job_id") or ""
                if not jid:
                    continue
                options[jid] = (f"{j.get('title') or '(untitled)'} — "
                                f"{j.get('company') or '?'} ({j.get('location') or '?'})")
            picked = st.multiselect(
                f"{len(options)} discovered job(s)",
                options=list(options.keys()),
                format_func=lambda jid: options.get(jid, jid),
                key=f"score_select_{wf_id}",
            )
            b_sel, cap_sel = st.columns([1, 3])
            if b_sel.button("Score selected", type="primary",
                            disabled=not picked, key=f"score_btn_{wf_id}"):
                try:
                    resp = api.submit_scoring_selection(wf_id, picked)
                    st.success(
                        f"Scoring {resp.get('scoring_count', len(picked))} selected "
                        "job(s). Switch to Live Run Monitor to watch progress, or "
                        "reopen this run when it finishes."
                    )
                except Exception as exc:
                    st.error(f"Could not start scoring: {exc}")
            cap_sel.caption(
                f"{len(picked)} selected. Unselected jobs are skipped (not scored)."
            )
        st.stop()

    # ── Find & Score — Pipeline table (jobs × stages) ─────────────────────────
    st.markdown("---")
    st.subheader("📍 Find & Score — jobs surfaced and ranked")
    st.caption("What came back from the search and how each job scored across the three career tracks. "
               "Jobs whose best track score meets your threshold automatically advance to deep review.")

    jobs_df = load_workflow_jobs(wf_id)
    if jobs_df.empty:
        st.info("No scored jobs yet for this run.")
    else:
        # ADR-057: hide-by-default toggle for excluded rows in this run.
        n_excluded = int(jobs_df["excluded"].fillna(0).sum()) if "excluded" in jobs_df.columns else 0
        show_excluded = False
        if n_excluded:
            show_excluded = st.toggle(
                f"Show {n_excluded} excluded job(s) in this run",
                value=False,
                key=f"show_excluded_{wf_id}",
            )
        if not show_excluded and "excluded" in jobs_df.columns:
            jobs_df = jobs_df[(jobs_df["excluded"].fillna(0) == 0)].reset_index(drop=True)

        view_df = jobs_df.copy()
        view_df["🚫"] = view_df["excluded"].fillna(0).apply(lambda v: "🚫" if v else "")
        view_df["✅ Reviewed"] = view_df["reviewed_at"].apply(_checked)
        view_df["✅ Advised"] = view_df["advised_at"].apply(_checked)
        view_df["✅ Prep"] = view_df["prep_at"].apply(_checked)

        ev = st.dataframe(
            view_df[[
                "🚫",
                "title", "company", "location", "url",
                "overall_score", "technical_score", "architecture_score", "leadership_score",
                "✅ Reviewed", "✅ Advised", "✅ Prep",
                "found_at", "scored_at",
            ]].rename(columns={
                "title": "Title", "company": "Company", "location": "Location", "url": "URL",
                "overall_score": "Overall",
                "technical_score": "Tech",
                "architecture_score": "Arch",
                "leadership_score": "Lead",
                "found_at": "Found", "scored_at": "Scored",
            }),
            key=f"jobs_table_{wf_id}",
            hide_index=True,
            use_container_width=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "🚫":      st.column_config.TextColumn("🚫", width="small",
                                                       help="🚫 = excluded from cross-run analytics + future discovery"),
                "URL":     st.column_config.LinkColumn("URL", width="small"),
                "Overall": st.column_config.ProgressColumn("Overall", min_value=0, max_value=100, format="%d"),
                "Tech":    st.column_config.ProgressColumn("Tech",    min_value=0, max_value=100, format="%d"),
                "Arch":    st.column_config.ProgressColumn("Arch",    min_value=0, max_value=100, format="%d"),
                "Lead":    st.column_config.ProgressColumn("Lead",    min_value=0, max_value=100, format="%d"),
            },
        )

        # ADR-057: per-row exclude / un-exclude action. Single-row selection
        # is the same affordance Workflow History uses, kept consistent.
        sel_rows = (ev.selection.rows if ev and getattr(ev, "selection", None) else []) or []
        sel_job: dict | None = None
        if sel_rows and sel_rows[0] < len(jobs_df):
            sel_job = jobs_df.iloc[sel_rows[0]].to_dict()

        ex_col1, ex_col2 = st.columns([1, 4])
        if sel_job:
            is_excluded = bool(sel_job.get("excluded") or 0)
            label = "♻ Un-exclude selected" if is_excluded else "🚫 Exclude selected"
            if ex_col1.button(label, key=f"excl_btn_{wf_id}", use_container_width=True):
                try:
                    if is_excluded:
                        api.unexclude_job(sel_job["job_id"])
                        st.success(f"Un-excluded: {sel_job.get('title', '')}")
                    else:
                        api.exclude_job(sel_job["job_id"])
                        st.success(f"Excluded: {sel_job.get('title', '')}")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as exc:
                    st.error(f"Action failed: {exc}")
            ex_col2.caption(
                f"Selected: **{sel_job.get('title','(untitled)')}** @ {sel_job.get('company','')}"
                + ("  ·  currently excluded" if is_excluded else "")
            )
        else:
            ex_col1.button("🚫 Exclude selected", disabled=True, use_container_width=True,
                           key=f"excl_btn_disabled_{wf_id}")
            ex_col2.caption("Select a row above to enable Exclude / Un-exclude.")

        # Drill into a specific job — picker + button
        st.markdown("**Drill into a job →**")
        _options = {
            f"{r['title']} @ {r['company']}  ·  overall {int(r['overall_score'])}": r["job_id"]
            for _, r in jobs_df.iterrows()
        }
        _label = st.selectbox(
            "Pick a job to see all of its outputs (scoring, every review round, advice, prep) with timestamps",
            options=list(_options.keys()),
            label_visibility="collapsed",
        )
        if st.button("Drill in ▶", key="drill_in_job"):
            _navigate("Job Detail",
                      detail_workflow_id=wf_id,
                      detail_job_id=_options[_label])

    # ── Review — deep critic + career advice (per job) ────────────────────────
    rev_df = load_deep_review_results(wf_id)
    if not rev_df.empty:
        st.markdown("---")
        st.subheader("📋 Review — deep analysis & career guidance")
        st.caption("Per-job critic + auditor output and the career advisor's positioning summary. "
                   "Resume gaps can be addressed via tailoring; career gaps must not be fabricated.")
        # Look up title/company/location and review/advice timestamps from jobs_df
        # so each expander header is human-readable (UUIDs are useless to the user).
        meta_by_job = {
            r["job_id"]: {
                "title":    r.get("title") or "(untitled)",
                "company":  r.get("company") or "",
                "location": r.get("location") or "",
                "reviewed": r.get("reviewed_at"),
                "advised":  r.get("advised_at"),
            }
            for _, r in (jobs_df.iterrows() if not jobs_df.empty else [])
        } if not jobs_df.empty else {}
        for _, row in rev_df.iterrows():
            jid = row["job_id"]
            meta = meta_by_job.get(jid, {})
            title = meta.get("title") or "(untitled)"
            company = meta.get("company") or ""
            location = meta.get("location") or ""
            ts_caption = []
            if meta.get("reviewed"):
                ts_caption.append(f"reviewed `{_fmt_ts(meta['reviewed'])}`")
            if meta.get("advised"):
                ts_caption.append(f"advised `{_fmt_ts(meta['advised'])}`")
            ts_str = "  ·  ".join(ts_caption)
            header = title
            if company:
                header += f" @ {company}"
            if location:
                header += f"  ·  {location}"
            summary = (row.get("overall_fit_summary") or "").strip()
            if summary:
                header += f" — {summary[:80]}"
            if ts_str:
                header += f"  ·  {ts_str}"
            with st.expander(header):
                c1, c2 = st.columns(2)
                c1.markdown("**Resume Gaps** *(can tailor)*")
                try:
                    for g in (json.loads(row.get("resume_only_gaps_json") or "[]") or []):
                        c1.markdown(f"- {g}")
                except Exception:
                    pass
                c2.markdown("**Career Gaps** *(must not fabricate)*")
                try:
                    for g in (json.loads(row.get("career_gaps_observed_json") or "[]") or []):
                        c2.markdown(f"- {g}")
                except Exception:
                    pass
                if row.get("positioning_summary"):
                    st.markdown(f"**Positioning:** {row['positioning_summary']}")
                if row.get("recommended_next_action"):
                    st.markdown(f"**Recommended:** {row['recommended_next_action']}")

    # ── Prep — interview readiness ────────────────────────────────────────────
    prep_df = load_interview_prep(wf_id)
    if not prep_df.empty:
        st.markdown("---")
        st.subheader("✨ Prep — interview readiness")
        st.caption("Likely topics, technical areas to brush up on, and a 7-day prep plan per qualifying job.")
        prep_meta = {
            r["job_id"]: {
                "title":    r.get("title") or "(untitled)",
                "company":  r.get("company") or "",
                "location": r.get("location") or "",
                "prep":     r.get("prep_at"),
            }
            for _, r in (jobs_df.iterrows() if not jobs_df.empty else [])
        } if not jobs_df.empty else {}
        for _, row in prep_df.iterrows():
            jid = row["job_id"]
            meta = prep_meta.get(jid, {})
            title = meta.get("title") or "(untitled)"
            company = meta.get("company") or ""
            location = meta.get("location") or ""
            header = title
            if company:
                header += f" @ {company}"
            if location:
                header += f"  ·  {location}"
            if meta.get("prep"):
                header += f"  ·  prep `{_fmt_ts(meta['prep'])}`"
            with st.expander(header):
                # Render every section the InterviewCoach produces. The previous
                # version only rendered topics + plan and dropped 4 sections
                # entirely; combined with the field-name bug below it, the user
                # never saw any prep output at all.
                def _render_list_section(label: str, json_key: str, *, bullets: bool = True) -> None:
                    try:
                        items = json.loads(row.get(json_key) or "[]")
                    except Exception:
                        return
                    if not items:
                        return
                    st.markdown(f"**{label}**")
                    if bullets:
                        for item in items:
                            st.markdown(f"- {item}")
                    else:
                        st.markdown(", ".join(items))

                _render_list_section("Likely interview topics", "likely_topics_json")
                _render_list_section("Technical topics to review", "technical_topics_json")
                _render_list_section("Leadership stories to prepare", "leadership_stories_json")
                _render_list_section("Weak areas to defend", "weak_areas_json")
                _render_list_section("Questions to ask the interviewer", "questions_to_ask_json")
                _render_list_section("7-day prep plan", "seven_day_plan_json")
                conf = row.get("confidence")
                if conf is not None:
                    try:
                        st.caption(f"Coach confidence: {int(conf)}%")
                    except (TypeError, ValueError):
                        pass

    # ── Prep — resume tailoring (on-demand per job, the action surface) ───────
    st.markdown("---")
    st.subheader("✨ Prep — tailored resume drafts + interview")
    st.caption(
        "Pick ANY scored job (ADR-061) and generate evidence-bound section "
        "suggestions, or prep for the interview. Every suggestion cites the "
        "original line in your resume; missing experience is labelled as a gap, "
        "never rewritten as if present. A job that was not auto-selected for deep "
        "review is deep-reviewed on demand first, so the cost includes a critic "
        "pass. Approve, edit, revise, or reject each draft to record your decision."
    )

    # ADR-061: tailoring + interview prep are available for any scored job, not
    # only the auto-selected top-3. Fall back to selected_jobs for older runs
    # whose state predates scored_jobs being carried through.
    scored_jobs_state = [
        j for j in (state.get("scored_jobs") or [])
        if j.get("status") == "scored"
    ] or (state.get("selected_jobs") or [])
    if not scored_jobs_state:
        st.info("No scored jobs in this run yet — tailoring and interview prep "
                "need at least one scored job.")
    else:
        try:
            with st.spinner("Loading tailoring drafts…"):
                tail_index = _cached_list_tailorings(wf_id)
        except Exception as exc:
            st.error(f"Could not load existing tailorings: {exc}")
            tail_index = []

        by_job: dict[str, list[dict]] = {}
        for t in tail_index:
            by_job.setdefault(t.get("job_id", ""), []).append(t)

        def _decide(tid: str, choice: str, edited: dict | None = None) -> None:
            try:
                api.submit_tailoring_decision(tid, choice, edited=edited)
                _cached_list_tailorings.clear()
                st.success(f"Decision saved: {choice}")
                st.rerun()
            except Exception as exc:
                st.error(f"Decision failed: {exc}")

        selected_ids = {
            (sj.get("job_id") or sj.get("id"))
            for sj in (state.get("selected_jobs") or [])
        }
        for sj in scored_jobs_state:
            jid = sj.get("job_id") or sj.get("id") or ""
            if not jid:
                continue
            jtitle = sj.get("title") or "(untitled)"
            jcompany = sj.get("company") or ""
            existing = by_job.get(jid, [])
            label = f"**{jtitle}** @ {jcompany}  ·  job `{jid[:8]}…`"
            if jid in selected_ids:
                label += "  ·  auto-selected"
            if existing:
                label += f"  ·  {len(existing)} draft(s)"
            with st.expander(label, expanded=False):
                if jid not in selected_ids:
                    st.caption(
                        "Not auto-selected for deep review — generating a draft "
                        "will run a deep-review pass on demand first (extra cost)."
                    )
                trig_col, prep_col, _ = st.columns([1, 1, 3])
                if trig_col.button("✨ Generate new draft", key=f"trig_tail_{jid}"):
                    with st.spinner("Tailoring + fidelity review (~60-90s, longer if deep-reviewing first)…"):
                        try:
                            api.trigger_tailoring(wf_id, jid)
                            _cached_list_tailorings.clear()
                            st.rerun()
                        except httpx.ReadTimeout:
                            # The synchronous server path can outlast the socket
                            # timeout (180s). The draft typically lands in
                            # tailored_resumes anyway -- check the list.
                            _cached_list_tailorings.clear()
                            st.warning(
                                "Client timed out, but the server may have completed the draft anyway. "
                                "Click the section header to collapse and reopen — the new draft should appear."
                            )
                        except Exception as exc:
                            st.error(f"Tailoring failed: {exc}")
                if prep_col.button("🎤 Prep for interview", key=f"trig_prep_{jid}"):
                    with st.spinner("Interview coach (~10-20s)…"):
                        try:
                            api.trigger_interview_prep(wf_id, jid)
                            st.success("Interview prep generated — see the readiness section above.")
                            st.rerun()
                        except httpx.ReadTimeout:
                            st.warning("Client timed out, but the prep may have completed. Reload to check.")
                        except Exception as exc:
                            st.error(f"Interview prep failed: {exc}")
                if not existing:
                    st.caption("No drafts yet for this job. Click **Generate new draft** to create one.")
                else:
                    rp_for_render = state.get("resume_profile") or None
                    for t in existing:
                        st.markdown("---")
                        _render_tailoring_card(t, _decide, resume_profile=rp_for_render)

    # ── Diagnostics — collapsed by default to keep the action surfaces above ──
    st.markdown("---")
    st.subheader("🔧 Diagnostics")
    st.caption("Settings used, cost breakdown, limits hit, and any errors. "
               "Collapsed by default — open if a run looks off.")

    cfg_used = state.get("effective_config") or {}
    sc = state.get("search_criteria") or {}
    cc = state.get("custom_urls") or []
    breakdown = compute_breakdown(wf_id)
    findings = analyze(state)
    errors = state.get("errors") or []

    with st.expander("Settings used for this run", expanded=False):
        if cfg_used or sc or cc:
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("**Search criteria**")
                st.json(sc, expanded=False)
                if cc:
                    st.markdown(f"**Custom URLs** ({len(cc)})")
                    for u in cc:
                        st.markdown(f"- {u}")
            with col_b:
                st.markdown("**Effective config**")
                st.json(cfg_used, expanded=False)
        else:
            st.caption("No settings snapshot stored for this run (likely a pre-snapshot legacy run).")

    if breakdown["rows"]:
        agg = breakdown["aggregate"]
        # Promoted out of a collapsed expander to its own visible subsection.
        # Cost is a primary operational concern; users shouldn't have to dig.
        st.markdown(f"#### 💰 Cost breakdown — ${agg['cost_usd']:.4f} across {agg['calls']} calls")
        st.caption(
            f"{agg['calls']} calls · {agg['tokens_input']:,} tokens in · "
            f"{agg['tokens_output']:,} tokens out · ~{int(agg['avg_latency_ms'])} ms avg latency. "
            "See the **Cost Dashboard** view for cross-run trends."
        )
        cost_df = pd.DataFrame(breakdown["rows"])
        # Side-by-side: bar chart and numeric table, so both visual and exact reads work.
        c_chart, c_table = st.columns([2, 3])
        with c_chart:
            chart_df = cost_df.sort_values("cost_usd", ascending=True)
            fig = px.bar(
                chart_df, x="cost_usd", y="agent_name", orientation="h",
                text="cost_usd",
                labels={"cost_usd": "Cost ($)", "agent_name": "Agent"},
                color="cost_usd", color_continuous_scale="oranges",
            )
            fig.update_traces(texttemplate="$%{x:.4f}", textposition="outside")
            fig.update_layout(margin=dict(l=10, r=10, t=10, b=10),
                              height=max(180, 32 * len(chart_df)),
                              coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)
        with c_table:
            tbl = cost_df.rename(columns={
                "agent_name": "Agent", "provider": "Provider", "model": "Model",
                "calls": "Calls", "tokens_input": "Tokens in",
                "tokens_output": "Tokens out", "cost_usd": "Cost ($)",
                "avg_latency_ms": "Avg latency",
            })
            st.dataframe(
                tbl, hide_index=True, use_container_width=True,
                column_config={
                    "Cost ($)": st.column_config.NumberColumn(format="$%.4f"),
                    "Avg latency": st.column_config.NumberColumn(format="%d ms"),
                },
            )

    # Limits & Constraints — keep open when something fired so the user notices
    _has_findings = bool(findings)
    with st.expander(
        f"Limits & Constraints" + (f" — {len(findings)} finding(s)" if _has_findings else ""),
        expanded=_has_findings,
    ):
        if not findings:
            st.success("No execution limits clipped this run.")
        else:
            for f in findings:
                (st.warning if f["severity"] == "warning" else st.info)(f["message"])

    # Errors — open when present
    if errors:
        with st.expander(f"Errors ({len(errors)})", expanded=True):
            for err in errors:
                st.json(err, expanded=False)


# ══════════════════════════════════════════════════════════════════════════════
# RESUME CLINIC (ADR-066) — standalone, job-agnostic resume review
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Resume Clinic":
    st.header("Resume Clinic")
    st.caption(
        "A job-agnostic resume review. Runs on the resume alone — no discovery, "
        "no scoring, no tailoring. Optional target role / track add the alignment axis."
    )

    user_id = st.session_state.current_user_id

    # ── Resume picker (active resume preselected) ────────────────────────────
    resumes_df = load_user_resumes(user_id)
    if resumes_df.empty:
        st.warning(
            "No resumes found for this profile. Upload one in Profiles, then return here."
        )
        st.stop()

    resume_label_by_id: dict[str, str] = {}
    for _, _row in resumes_df.iterrows():
        _flag = " (active)" if int(_row.get("is_active") or 0) else ""
        resume_label_by_id[str(_row["resume_id"])] = (
            f"{_row.get('file_name') or _row['resume_id']}  ·  v{_row.get('version') or '?'}{_flag}"
        )

    active_row = resumes_df[resumes_df["is_active"] == 1].head(1)
    default_resume_id = str(active_row.iloc[0]["resume_id"]) if not active_row.empty else str(
        resumes_df.iloc[0]["resume_id"]
    )

    rc_form_col, rc_results_col = st.columns([1, 2])
    with rc_form_col:
        st.subheader("Run a clinic")
        sel_resume_id = st.selectbox(
            "Resume",
            options=list(resume_label_by_id.keys()),
            format_func=lambda rid: resume_label_by_id.get(rid, rid),
            index=list(resume_label_by_id.keys()).index(default_resume_id),
            key="rc_resume_id",
        )

        # Pre-fill target role from profile.search_criteria.roles[0] if available
        prefill_role = ""
        try:
            _cfg = _get_config_cached().get("effective_config", {}) or {}
            _roles = (_cfg.get("search", {}) or {}).get("titles") or []
            if _roles:
                prefill_role = str(_roles[0])
        except Exception:
            prefill_role = ""
        target_role = st.text_input(
            "Target role (optional)",
            value=prefill_role,
            placeholder="e.g. entry-level security analyst",
            help=(
                "Free text. Adds the alignment axis (missing skills / keywords / "
                "certifications). Leave blank for quality-only mode."
            ),
            key="rc_target_role",
        )
        target_track = st.selectbox(
            "Target track (optional)",
            options=["", "ic", "architect", "management"],
            format_func=lambda x: "—" if x == "" else x.upper() if x == "ic" else x.title(),
            key="rc_target_track",
        )
        seniority_aware = st.toggle(
            "Seniority-aware feedback",
            value=False,
            help=(
                "When on, the reviewer calibrates findings, fixes, and rewrites to "
                "the candidate's career stage as inferred from the resume "
                "(early-career: project/education-forward; senior+: scope and outcomes)."
            ),
            key="rc_seniority_aware",
        )
        run_clicked = st.button("Run clinic", type="primary", use_container_width=True)

    if run_clicked:
        try:
            with st.spinner("Running clinic review… (resume reviewer + fidelity)"):
                row = api.run_resume_clinic(
                    user_id,
                    resume_id=sel_resume_id,
                    target_role=target_role.strip() or None,
                    target_track=target_track or None,
                    seniority_aware=bool(seniority_aware),
                )
            st.session_state.rc_last_review = row
            st.success("Clinic review complete.")
        except Exception as exc:
            st.error(f"Clinic failed: {exc}")
            st.session_state.rc_last_review = None

    # ── Results pane ─────────────────────────────────────────────────────────
    with rc_results_col:
        review = st.session_state.get("rc_last_review")
        if not review:
            st.info("Pick a resume and click **Run clinic** to start.")
        else:
            _quality = review.get("quality") or {}
            _alignment = review.get("alignment") or None
            _overhaul = review.get("overhaul") or {}
            _fid = review.get("fidelity_review") or None

            st.subheader("Quality scorecard")
            st.caption(_quality.get("overall_summary") or "")
            _dims = _quality.get("dimensions") or []
            if _dims:
                _rating_chip = {
                    "strong": "🟢 strong",
                    "adequate": "🟡 adequate",
                    "needs_work": "🔴 needs work",
                }
                for _d in _dims:
                    _name = (_d.get("dimension") or "").replace("_", " ").title()
                    _rating = _rating_chip.get(_d.get("rating", ""), _d.get("rating", ""))
                    with st.expander(f"{_name}  ·  {_rating}", expanded=False):
                        _findings = _d.get("findings") or []
                        _fixes = _d.get("fixes") or []
                        if _findings:
                            st.markdown("**Findings**")
                            for _f in _findings:
                                st.markdown(f"- {_f}")
                        if _fixes:
                            st.markdown("**Fixes**")
                            for _f in _fixes:
                                st.markdown(f"- {_f}")

            if _alignment:
                st.subheader("Role / track alignment")
                st.caption(_alignment.get("fit_summary") or "")
                _conf = (_alignment.get("confidence") or "").title()
                st.caption(f"Confidence: **{_conf}**")
                _cols = st.columns(2)
                with _cols[0]:
                    if _alignment.get("missing_skills"):
                        st.markdown("**Missing skills**")
                        for _s in _alignment["missing_skills"]:
                            st.markdown(f"- {_s}")
                    if _alignment.get("missing_keywords"):
                        st.markdown("**Missing keywords**")
                        for _s in _alignment["missing_keywords"]:
                            st.markdown(f"- {_s}")
                    if _alignment.get("emphasize"):
                        st.markdown("**Emphasize on the resume**")
                        for _s in _alignment["emphasize"]:
                            st.markdown(f"- {_s}")
                with _cols[1]:
                    if _alignment.get("suggested_certifications"):
                        st.markdown("**Suggested certifications**")
                        for _s in _alignment["suggested_certifications"]:
                            st.markdown(f"- {_s}")
                    if _alignment.get("suggested_projects"):
                        st.markdown("**Suggested projects**")
                        for _s in _alignment["suggested_projects"]:
                            st.markdown(f"- {_s}")

            _reorg = _overhaul.get("reorganization") or {}
            if _reorg:
                st.subheader("Reorganization plan")
                _order = _reorg.get("section_order") or []
                if _order:
                    st.markdown("**Proposed section order:** " + " → ".join(_order))
                _moves = _reorg.get("moves") or []
                if _moves:
                    _action_chip = {"move": "↕️", "cut": "🗑️", "promote": "⬆️"}
                    for _m in _moves:
                        st.markdown(
                            f"{_action_chip.get(_m.get('action'), '•')} "
                            f"**{(_m.get('action') or '').title()}** · "
                            f"{_m.get('subject') or ''}  ·  _{_m.get('rationale') or ''}_"
                        )

            _rewrites = _overhaul.get("rewrites") or []
            if _rewrites:
                st.subheader("Rewrites")
                _ct_chip = {
                    "restate":  "🔁 restate",
                    "reorder":  "↔ reorder",
                    "quantify": "🔢 quantify",
                    "reframe":  "🎯 reframe",
                }
                for _i, _r in enumerate(_rewrites):
                    _ct = _r.get("claim_type") or "restate"
                    st.markdown(
                        f"_Suggestion {_i + 1}_  ·  {_ct_chip.get(_ct, _ct)}  ·  "
                        f"_{_r.get('section_label') or ''}_"
                    )
                    _ca, _cb = st.columns(2)
                    _ca.markdown("_Original_")
                    _ca.markdown(f"> {_r.get('original_text') or '_(net-new line)_'}")
                    _cb.markdown("_Suggested_")
                    _cb.markdown(f"> {_r.get('suggested_text') or '—'}")
                    _ev = (_r.get("supporting_evidence") or "").strip()
                    if _ev:
                        st.caption(f"📎 Evidence from your resume: _{_ev}_")
                    st.markdown("")

            if _fid:
                st.subheader("Fidelity check")
                _verdict = _fid.get("approval_recommendation") or "—"
                _verdict_chip = {
                    "approve": "🟢 approve",
                    "revise":  "🟡 revise",
                    "reject":  "🔴 reject",
                }.get(_verdict, _verdict)
                st.markdown(f"**Recommendation:** {_verdict_chip}  ·  confidence **{_fid.get('confidence', 0)}**")
                _unsupported = _fid.get("unsupported_claims") or []
                _fabricated = _fid.get("fabricated_metrics") or []
                if _unsupported:
                    st.warning("Unsupported claims flagged:\n\n" + "\n".join(f"- {x}" for x in _unsupported))
                if _fabricated:
                    st.warning("Fabricated metrics flagged:\n\n" + "\n".join(f"- {x}" for x in _fabricated))
                st.caption(
                    "Note: the fidelity reviewer is tailoring-tuned; some of its "
                    "checks (length budget, impact rationale, strategy summary) "
                    "apply less cleanly to clinic rewrites. A clinic-tuned "
                    "fidelity prompt is a documented fast-follow."
                )

            # ── Decision controls ────────────────────────────────────────────
            st.markdown("---")
            st.subheader("Decision")
            _decision_now = (review or {}).get("decision")
            if _decision_now:
                st.caption(f"Decision on record: **{_decision_now}** at {review.get('decided_at') or '—'}")
            _dc1, _dc2, _dc3 = st.columns(3)
            _clinic_id = review.get("clinic_id")
            if _dc1.button("✅ Approve", key="rc_dec_approve", use_container_width=True):
                try:
                    _updated = api.submit_resume_clinic_decision(_clinic_id, "approve")
                    st.session_state.rc_last_review = _updated
                    st.success("Approved.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not record decision: {exc}")
            if _dc2.button("✏ Edit / send revise", key="rc_dec_revise", use_container_width=True):
                try:
                    _updated = api.submit_resume_clinic_decision(_clinic_id, "revise")
                    st.session_state.rc_last_review = _updated
                    st.info("Marked for revision.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not record decision: {exc}")
            if _dc3.button("❌ Reject", key="rc_dec_reject", use_container_width=True):
                try:
                    _updated = api.submit_resume_clinic_decision(_clinic_id, "reject")
                    st.session_state.rc_last_review = _updated
                    st.warning("Rejected.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not record decision: {exc}")

            # ── Refine with feedback (ADR-068) ───────────────────────────────
            st.markdown("---")
            st.subheader("Refine with feedback")
            st.caption(
                "Iteratively revise the overhaul through chat. Each turn updates "
                "the preview in place; click **Save final edit** when you're done "
                "to lock the result in as your final draft, or **Discard chat edits** "
                "to revert to the agent's original overhaul."
            )

            # Live preview of the current state. compose_resume reads the
            # edited overhaul whenever populated (per ADR-068), so the preview
            # reflects the latest chat turn automatically.
            _rc_preview_review = st.session_state.get("rc_last_review") or {}
            _rc_overhaul = _rc_preview_review.get("overhaul")
            _rc_edited = _rc_preview_review.get("edited")
            _rc_decision = _rc_preview_review.get("decision")
            _rc_resume_id = _rc_preview_review.get("resume_id")

            # Fetch the parsed profile fresh from the DB so we render against
            # the same data the backend chat agent saw. Falls back to an
            # empty dict if the resume can't be loaded (the chat will still
            # work but the preview may be sparse).
            _rc_profile_dict: dict = {}
            if _rc_resume_id:
                try:
                    import sqlite3
                    import json as _json
                    from app.ui.db_reader import DB_PATH as _DBP
                    _db = sqlite3.connect(str(_DBP))
                    _db.row_factory = sqlite3.Row
                    _row = _db.execute(
                        "SELECT parsed_profile_json FROM resumes WHERE id = ?",
                        (_rc_resume_id,),
                    ).fetchone()
                    if _row and _row["parsed_profile_json"]:
                        _rc_profile_dict = _json.loads(_row["parsed_profile_json"])
                    _db.close()
                except Exception:
                    _rc_profile_dict = {}

            from app.services.resume_text_renderer import compose_resume as _compose_resume, render_markdown as _render_markdown
            try:
                _rc_rendered = _compose_resume(
                    _rc_profile_dict, _rc_overhaul, _rc_edited, _rc_decision,
                )
                _rc_markdown = _render_markdown(_rc_rendered)
            except Exception as _e:
                _rc_markdown = f"_Preview unavailable: {_e}_"

            with st.expander("Live preview", expanded=True):
                st.markdown(_rc_markdown)

            # ── Session cost meter (ADR-068) ────────────────────────────────
            # Surfaces `turns_used / max_turns` and `session_cost_usd` returned
            # by the last /chat call. Stays sticky between turns so the user
            # sees their remaining budget before sending the next message.
            _rc_cost_key = f"rc_chat_cost_{_clinic_id}"
            _rc_cost = st.session_state.get(_rc_cost_key)
            if _rc_cost:
                _turns_used = int(_rc_cost.get("turns_used") or 0)
                _max_turns = int(_rc_cost.get("max_turns") or 0)
                _sess_cost = float(_rc_cost.get("session_cost_usd") or 0.0)
                _pct = (_turns_used / _max_turns) if _max_turns else 0.0
                _cm1, _cm2 = st.columns([3, 2])
                _cm1.progress(min(_pct, 1.0),
                              text=f"Chat turns: {_turns_used} / {_max_turns}")
                _cm2.metric("Session cost", f"${_sess_cost:.4f}")
                if _pct >= 0.95:
                    st.error(
                        f"You've used {_turns_used} of {_max_turns} chat turns. "
                        "The next turn may be blocked - approve / edit your current "
                        "draft, or start a new clinic for more iterations."
                    )
                elif _pct >= 0.75:
                    st.warning(
                        f"You've used {_turns_used} of {_max_turns} chat turns. "
                        "Consider locking in your edit soon."
                    )

            # ── Chat input ──────────────────────────────────────────────────
            _rc_section_options = {
                "whole": "Whole resume",
                "headline": "Headline",
                "summary": "Summary",
                "experience": "Experience",
                "skills": "Skills",
                "education": "Education",
                "certifications": "Certifications",
            }
            _rc_section = st.selectbox(
                "Section to focus on",
                options=list(_rc_section_options.keys()),
                format_func=lambda k: _rc_section_options[k],
                key=f"rc_chat_section_{_clinic_id}",
            )
            _rc_message = st.text_area(
                "What would you like to change?",
                placeholder=(
                    "e.g. \"make the summary shorter and front-load the "
                    "cybersecurity angle\" or \"promote my projects above experience\""
                ),
                key=f"rc_chat_msg_{_clinic_id}",
                height=80,
            )

            _rc_chat_history_key = f"rc_chat_history_{_clinic_id}"
            if _rc_chat_history_key not in st.session_state:
                st.session_state[_rc_chat_history_key] = []

            _cc1, _cc2, _cc3 = st.columns([2, 2, 2])
            if _cc1.button("Send feedback", type="primary",
                           disabled=not _rc_message.strip(),
                           key=f"rc_chat_send_{_clinic_id}",
                           use_container_width=True):
                try:
                    with st.spinner("Revising…"):
                        _chat_resp = api.chat_resume_clinic(
                            _clinic_id,
                            _rc_message.strip(),
                            section=_rc_section,
                            history=st.session_state[_rc_chat_history_key],
                        )
                    # Append to in-session history.
                    st.session_state[_rc_chat_history_key].append(
                        {"role": "user", "message": _rc_message.strip()},
                    )
                    st.session_state[_rc_chat_history_key].append(
                        {"role": "assistant",
                         "message": _chat_resp.get("reply") or ""},
                    )
                    # Stash the cost meter fields so they survive the rerun.
                    st.session_state[_rc_cost_key] = {
                        "turns_used":      _chat_resp.get("turns_used", 0),
                        "max_turns":       _chat_resp.get("max_turns", 0),
                        "session_cost_usd": _chat_resp.get("session_cost_usd", 0.0),
                    }
                    # Refresh the clinic row so the preview re-renders.
                    try:
                        _rows = api.list_resume_clinic_runs(user_id).get("reviews") or []
                        _updated = next(
                            (r for r in _rows if r.get("clinic_id") == _clinic_id),
                            None,
                        )
                        if _updated:
                            st.session_state.rc_last_review = _updated
                    except Exception:
                        pass
                    st.rerun()
                except httpx.HTTPStatusError as exc:
                    # Surface the cap-reached reason directly when the backend
                    # returns 429 (chat_turn_cap_reached) so the user doesn't
                    # see a raw HTTP error message.
                    _detail = None
                    try:
                        _detail = (exc.response.json() or {}).get("detail")
                    except Exception:
                        pass
                    if exc.response.status_code == 429:
                        st.error(
                            _detail or
                            "Chat turn cap reached for this clinic. Approve / edit "
                            "your current draft, or start a new clinic."
                        )
                    else:
                        st.error(f"Chat turn failed: {_detail or exc}")
                except Exception as exc:
                    st.error(f"Chat turn failed: {exc}")

            if _cc2.button("✓ Save final edit",
                           key=f"rc_chat_save_{_clinic_id}",
                           use_container_width=True,
                           help="Lock the current chat-edited state as your final draft (decision = edit)."):
                try:
                    _edited_payload = _rc_edited or _rc_overhaul or {}
                    _updated = api.submit_resume_clinic_decision(
                        _clinic_id, "edit", edited=_edited_payload,
                    )
                    st.session_state.rc_last_review = _updated
                    st.success("Saved as final edit.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Save failed: {exc}")

            if _cc3.button("↺ Discard chat edits",
                           key=f"rc_chat_discard_{_clinic_id}",
                           use_container_width=True,
                           help="Clear chat edits and decision; revert to the agent's original overhaul."):
                try:
                    api.discard_resume_clinic_edits(_clinic_id)
                    # Refresh from server.
                    _rows = api.list_resume_clinic_runs(user_id).get("reviews") or []
                    _updated = next(
                        (r for r in _rows if r.get("clinic_id") == _clinic_id),
                        None,
                    )
                    if _updated:
                        st.session_state.rc_last_review = _updated
                    st.session_state[_rc_chat_history_key] = []
                    # Discard only clears edits on the server; the chat-turn
                    # spend on the workflow_run_id is permanent (it's already
                    # billed in llm_calls). Leave the meter intact so the user
                    # sees the true session cost.
                    st.info("Chat edits discarded.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Discard failed: {exc}")

            # ── Conversation log ────────────────────────────────────────────
            _hist = st.session_state.get(_rc_chat_history_key) or []
            if _hist:
                with st.expander(f"Conversation ({len(_hist) // 2} turn(s))", expanded=False):
                    for _msg in _hist:
                        _role = _msg.get("role", "")
                        _text = _msg.get("message", "")
                        if _role == "user":
                            st.markdown(f"**You:** {_text}")
                        else:
                            st.markdown(f"**Agent:** _{_text}_")

            # ── Export the final resume ──────────────────────────────────────
            st.markdown("---")
            st.subheader("Export the final resume")
            st.caption(
                "Decision-aware: approve uses the agent's overhaul, edit uses "
                "your draft, reject renders your original resume. No decision "
                "shows a preview-banner copy."
            )
            _fmt_labels = {
                "md":   "Markdown (.md)",
                "txt":  "Plain text (.txt)",
                "html": "HTML (.html)",
                "json": "JSON Resume (.json)",
                "docx": "Word (.docx)",
                "pdf":  "PDF (.pdf)",
            }
            _fmt = st.selectbox(
                "Format",
                options=list(_fmt_labels.keys()),
                format_func=lambda f: _fmt_labels[f],
                key="rc_export_format",
            )
            try:
                _bytes, _ctype, _fname = api.export_resume_clinic(_clinic_id, _fmt)
            except Exception as exc:
                _bytes, _ctype, _fname = None, None, None
                st.error(f"Could not generate export: {exc}")
            if _bytes is not None:
                # Inline preview for the text-y formats so the user sees what
                # will be downloaded before clicking.
                if _fmt in ("md", "txt"):
                    with st.expander("Preview", expanded=False):
                        st.code(_bytes.decode("utf-8", errors="replace"), language=("markdown" if _fmt == "md" else None))
                elif _fmt == "json":
                    with st.expander("Preview", expanded=False):
                        st.code(_bytes.decode("utf-8", errors="replace"), language="json")
                elif _fmt == "html":
                    with st.expander("Preview (raw HTML)", expanded=False):
                        st.code(_bytes.decode("utf-8", errors="replace"), language="html")
                st.download_button(
                    label=f"⬇ Download {_fmt_labels[_fmt]}",
                    data=_bytes,
                    file_name=_fname or f"resume_clinic_{_clinic_id[:8]}.{_fmt}",
                    mime=_ctype or "application/octet-stream",
                    use_container_width=True,
                    key=f"rc_dl_{_clinic_id}_{_fmt}",
                )

    # ── Past runs ────────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Past clinic runs")
    try:
        past = load_user_clinic_reviews(user_id)
    except Exception:
        past = None
    if past is None or past.empty:
        st.caption("No past clinic runs for this profile yet.")
    else:
        for _, _row in past.iterrows():
            _label_bits = [
                _fmt_ts(_row.get("created_at")),
                _row.get("target_role") or "no target",
                _row.get("target_track") or "—",
                (_row.get("decision") or "no decision"),
            ]
            with st.expander(" · ".join(_label_bits)):
                st.caption(f"clinic_id `{_row.get('clinic_id')}`  ·  resume `{_row.get('resume_id')}`")
                if st.button("Load into results pane", key=f"rc_load_{_row.get('clinic_id')}"):
                    try:
                        _rows = api.list_resume_clinic_runs(user_id).get("reviews") or []
                        _target = next((r for r in _rows if r.get("clinic_id") == _row.get("clinic_id")), None)
                        if _target:
                            st.session_state.rc_last_review = _target
                            st.rerun()
                    except Exception as exc:
                        st.error(f"Could not load past run: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Settings":
    st.header("Settings")

    cfg = _get_config_cached()
    eff = cfg.get("effective_config", {}) or {}
    protected = set(cfg.get("protected_keys", []) or [])
    if cfg.get("_offline_reason"):
        st.warning(f"Backend not reachable — read-only fallback. ({cfg['_offline_reason']})")

    st.caption(
        "Edit the values below to update your defaults. Protected keys (LLM models, "
        "execution limits, retention windows) are read-only and live in `config/config.yaml`."
    )

    def _save(key: str, value: object) -> None:
        """Persist via PUT /config, then POST /config/reload so the backend
        picks up the change without a manual restart (ADR-053 addendum)."""
        try:
            api.put_config(key, value)
            st.session_state.config_cache = None
        except Exception as exc:
            st.error(f"Save failed for `{key}`: {exc}")
            return
        # Reload the backend so the change is live for the next workflow run.
        # Per-agent assignment changes especially need this — ModelRegistry
        # caches one provider per (provider, model) at startup.
        try:
            reload_result = api.reload_config()
            if key.startswith("agents."):
                # Surface the new effective assignment so the user can confirm
                # the agent now points at the chosen model.
                assignment = (reload_result or {}).get("agent_assignment") or {}
                # Extract the agent_name from "agents.{name}..." for the toast.
                parts = key.split(".")
                if len(parts) >= 2:
                    a = parts[1]
                    if a in assignment:
                        m = assignment[a]
                        st.success(
                            f"Saved `{key}` and applied. "
                            f"Active: **{a}** → `{m['provider']}/{m['model']}`"
                        )
                        return
            st.success(f"Saved `{key}` and applied (no restart needed).")
        except Exception as exc:
            st.warning(
                f"Saved `{key}` but the live reload failed: {exc}. "
                "Restart `uvicorn` to apply the change."
            )

    # ── Search ─────────────────────────────────────────────────────────────
    st.subheader("Search")
    search = (eff.get("search") or {}).copy()

    titles_str = st.text_area(
        "search.titles (comma-separated)",
        value=", ".join(search.get("titles", [])),
        height=80,
    )
    if st.button("Save titles"):
        _save("search.titles",
              [t.strip() for t in titles_str.split(",") if t.strip()])

    locations_str = st.text_area(
        "search.locations (comma-separated)",
        value=", ".join(search.get("locations", [])),
        height=60,
    )
    if st.button("Save locations"):
        _save("search.locations",
              [l.strip() for l in locations_str.split(",") if l.strip()])

    max_discovered = st.number_input(
        "search.max_discovered (manual-mode discovery net width)",
        min_value=1, max_value=50,
        value=int(search.get("max_discovered", 50)),
        help="ADR-061: how many jobs to surface for triage when manual selection "
             "is on. Default 50, ceiling 50. Ignored in auto mode.",
    )
    if st.button("Save max_discovered"):
        _save("search.max_discovered", int(max_discovered))

    # ── Scoring ────────────────────────────────────────────────────────────
    st.subheader("Scoring")
    scoring = (eff.get("scoring") or {}).copy()
    threshold = st.slider(
        "scoring.min_match_score (any track ≥ this triggers deep review)",
        min_value=0, max_value=100,
        value=int(scoring.get("min_match_score", 75)),
        step=5,
    )
    if st.button("Save min_match_score"):
        _save("scoring.min_match_score", int(threshold))

    max_scored = st.number_input(
        "scoring.max_scored (how many jobs get research + scoring)",
        min_value=1, max_value=25,
        value=int(scoring.get("max_scored", 10)),
        help="ADR-061: the funnel's scored width. Default 10, ceiling 25. In auto "
             "mode this is also the discovery cap; runs can override it.",
    )
    if st.button("Save max_scored"):
        _save("scoring.max_scored", int(max_scored))

    manual_selection_default = st.checkbox(
        "scoring.manual_selection (review discovered jobs before paying to score them)",
        value=bool(scoring.get("manual_selection", False)),
        help="ADR-060: when on, discovery casts a wider net and runs park at a "
             "selection screen so you choose which jobs are worth the research + "
             "scoring spend. This sets the default; each run can still override it "
             "on the Start New Run form.",
    )
    if st.button("Save manual_selection"):
        _save("scoring.manual_selection", bool(manual_selection_default))

    # ── Agent Models (per ADR-053) ─────────────────────────────────────────
    st.markdown("---")
    st.subheader("Agent Models")
    st.caption(
        "Pick a provider and model per agent. Indicative cost shown per million tokens. "
        "Saves trigger a live reload of the backend's agent bindings — no manual "
        "restart needed for runtime overrides. In-flight workflows keep their "
        "original assignment; only NEW workflows pick up the change."
    )

    with st.spinner("Loading provider catalog…"):
        providers_payload = _cached_get_providers()
    if providers_payload is None:
        st.warning("Couldn't reach `/config/providers` (backend may be down or restarting).")

    if providers_payload:
        catalog = providers_payload.get("providers", {}) or {}
        agent_assignment = providers_payload.get("agent_assignment", {}) or {}
        meta = catalog.get("_meta", {}) or {}
        high_volume_agents = set(meta.get("high_volume_agents") or [])
        high_volume_safe_models = set(meta.get("high_volume_safe_models") or [])

        if not catalog.get("openai", {}).get("available", False):
            st.info(
                "OpenAI provider is not registered (no `OPENAI_API_KEY` in `.env`). "
                "Add the key and restart the backend to enable OpenAI models."
            )

        # One row per agent; provider dropdown then a model dropdown filtered by it.
        # Iterate only over real agent names; the catalog's "_meta" key is sidecar metadata.
        for agent_name in sorted(a for a in agent_assignment.keys() if not a.startswith("_")):
            assignment = agent_assignment[agent_name]
            current_provider = assignment.get("provider", "claude")
            current_model = assignment.get("model", "")
            cost_capped = agent_name in high_volume_agents

            with st.expander(
                f"`{agent_name}`  ·  current: **{current_provider}** / `{current_model}`"
                + ("  ·  💰 cost-capped" if cost_capped else ""),
                expanded=False,
            ):
                if cost_capped:
                    st.caption(
                        "**Cost-capped agent.** This agent runs on every job (10-20 "
                        "calls per workflow), so its model is restricted to the "
                        f"cheapest tier: `{', '.join(sorted(high_volume_safe_models))}`. "
                        "Cost here is a design decision; expensive models are "
                        "reserved for low-volume, user-facing agents."
                    )

                # Provider options — only show those the server reports as available.
                # For cost-capped agents, also restrict to providers that have at
                # least one allowed model.
                def _has_allowed_model(provider_id: str) -> bool:
                    if not cost_capped:
                        return True
                    return any(
                        m["id"] in high_volume_safe_models
                        for m in (catalog.get(provider_id, {}).get("models") or [])
                    )

                provider_options = [
                    p for p, info in catalog.items()
                    if not p.startswith("_")
                    and (info.get("available", False) or p == current_provider)
                    and _has_allowed_model(p)
                ]
                provider_choice = st.selectbox(
                    "Provider",
                    options=provider_options,
                    index=provider_options.index(current_provider) if current_provider in provider_options else 0,
                    key=f"prov_{agent_name}",
                )

                # Model options for the chosen provider, filtered by cost cap.
                model_entries = catalog.get(provider_choice, {}).get("models", []) or []
                if cost_capped:
                    model_entries = [m for m in model_entries if m["id"] in high_volume_safe_models]
                model_ids = [m["id"] for m in model_entries]
                model_idx = model_ids.index(current_model) if current_model in model_ids else 0
                model_choice = st.selectbox(
                    "Model",
                    options=model_ids,
                    index=model_idx if model_ids else 0,
                    format_func=lambda mid: _label_with_cost(mid, model_entries),
                    key=f"model_{agent_name}",
                )

                if st.button("Save", key=f"save_{agent_name}"):
                    try:
                        api.put_config(f"agents.{agent_name}.provider", provider_choice)
                        api.put_config(f"agents.{agent_name}.model", model_choice)
                        st.session_state.config_cache = None
                        _cached_get_providers.clear()
                        st.success(
                            f"Saved {agent_name} → {provider_choice}/{model_choice}. "
                            "Restart the backend for it to take effect."
                        )
                    except Exception as exc:
                        st.error(f"Save failed: {exc}")

    # ── Read-only protected ────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Read-only (protected)")
    st.caption("These cannot be changed via the UI to prevent accidental cost spikes or instability.")
    st.json({k: _get_nested(eff, k.split(".")) for k in sorted(protected)
             if _get_nested(eff, k.split(".")) is not None}, expanded=False)

    # ── Data retention purge (ADR-070) ───────────────────────────────────────
    st.subheader("Data retention")
    st.caption(
        "Delete data past the retention windows above. A purged workflow run takes "
        "ALL its rows with it (scores, reviews, advice, prep, tailorings, clinic "
        "reviews, decisions, observability); inactive resumes are removed only once "
        "no surviving run still references them. The windows are read-only and live "
        "in `config/config.yaml`. Purge is explicit and never runs automatically."
    )
    with st.expander("Run data-retention purge"):
        st.warning(
            "This permanently deletes rows older than the retention windows. It "
            "cannot be undone. Make sure you have a backup of `data/v2.db` if you "
            "might want this data back."
        )
        confirm = st.checkbox(
            "I understand this permanently deletes data past the retention windows.",
            key="purge_confirm",
        )
        if st.button("Run purge now", type="primary", disabled=not confirm,
                     key="purge_run_btn"):
            try:
                with st.spinner("Purging..."):
                    result = api.purge_data()
            except Exception as exc:
                st.error(f"Purge failed: {exc}")
            else:
                deleted = {t: n for t, n in (result or {}).items() if n}
                total = sum(deleted.values())
                if total:
                    st.success(f"Purged {total} rows.")
                    st.json(deleted, expanded=True)
                else:
                    st.info("Nothing was past the retention windows; no rows deleted.")
                # The history/cost views read from the DB directly — invalidate any
                # cached config so a re-render reflects the smaller dataset.
                st.session_state.config_cache = None
