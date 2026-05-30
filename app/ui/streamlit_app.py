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
)
# Cached data-access wrappers extracted to app/ui/data.py (UI refactor Phase 2).
# (_load_yaml_config also lives in data.py but is only used there by
# _get_config_cached, so the entrypoint does not import it.)
from app.ui.data import (
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
