"""Opportunity - the single per-job surface (ADR-088 Tier 2, Phase 5).

One page per job that opens on any job selection (Matches "Open opportunity", a
Search-detail job row, the inline picker). It merges what used to be split across
two screens:

- the **read** side (formerly Job Detail): fit summary, score, resume-gap vs
  career-gap, deep-review rounds, career advice, interview prep; and
- the **action** side (formerly Workflow Detail's per-job region): deep review on
  demand, the FULL tailoring flow (generate draft -> drafts list -> approve / revise
  / reject / edit decisions -> ADR-072 live chat + export), interview prep on
  demand, plus the cost / "already-run" legibility.

Guardrail (ADR-088 section E, CLAUDE.md): this page offers **preparation** (tailor,
interview prep) and **filtering** (exclude = "hide from future searches") only. It
must NOT grow Apply / Save / application-status fields, nor a pursuing / shortlist /
saved set - the career decision point stays human-owned. A guardrail test scans this
module for those words.
"""
from __future__ import annotations

import json

import httpx
import streamlit as st

import app.ui.api_client as api
from app.ui.components.bullets import _bullets, _para
from app.ui.components.favorites import render_favorite_toggle
from app.ui.components.posting_link import render_posting_links
from app.ui.components.tailoring_panel import render_job_tailoring
from app.ui.data import (
    _cached_job_pipeline,
    _cached_recent_workflows,
    _cached_workflow_detail,
    _cached_workflow_jobs,
)
from app.ui.formatting import _fmt_ts, format_posting_age, source_label
from app.ui.nav import ViewContext, _navigate, back_button
from app.workflows.limits import TRACK_TO_SCORE_KEY, get_active_tracks

# Rough, directional cost hints (USD) for the on-demand actions, so the user sees
# the spend before triggering it. Not a quote - actual cost lands in the run rollup.
_COST_HINT = {"deep_review": "~$0.04", "tailor": "~$0.02", "interview": "~$0.03"}
_TRACK_LABEL = {"ic": "Technical", "architect": "Architecture", "management": "Leadership"}


def render(ctx: ViewContext) -> None:
    wf_id = st.session_state.detail_workflow_id
    job_id = st.session_state.detail_job_id

    if not wf_id or not job_id:
        _picker()
        return

    pipeline = _cached_job_pipeline(wf_id, job_id)
    job = pipeline.get("job") or {}
    if not job:
        back_button("Matches")
        st.error(f"No job row found for `{job_id}`. It may have been purged or never persisted.")
        return

    record = _cached_workflow_detail(wf_id)
    state = (record or {}).get("state") or {}

    _header(wf_id, job_id, job, state)
    _why_and_gaps(pipeline, state)
    _deep_review(wf_id, job_id, pipeline)
    _next_steps(wf_id, job_id, state)
    _interview_prep_read(pipeline)
    _more_detail(pipeline)


# ── Header: back / exclude / title / best-track context ───────────────────────

def _header(wf_id: str, job_id: str, job: dict, state: dict) -> None:
    top_l, top_fav, top_r = st.columns([3, 1, 1])
    with top_l:
        back_button("Matches")
    with top_fav:
        # ADR-090: favorite this job for the Resume Clinic (a tailoring target).
        render_favorite_toggle(job_id=job_id, workflow_id=wf_id, key="opp_favorite")
    with top_r:
        _exclude_control(wf_id, job_id, job)

    st.header(job.get("title") or "(untitled)")
    bits = [job.get("company") or "—", job.get("location") or "—"]
    src = job.get("source")
    if src:
        bits.append(f"source `{src}`")
    st.markdown("  ·  ".join(str(b) for b in bits))

    # ADR-093: posting-link reliability. Show the source (employer-direct vs an
    # expiring aggregator link) and, when the link is unreliable or the posting is
    # stale, offer a "find the live posting" web-search fallback so a dead link is
    # never a dead end.
    from app.services.posting_age_filter import is_stale
    _stale = is_stale(job.get("posted_at"))
    render_posting_links(job, key="opp_posting", stale=_stale, container_width=False)

    # Best active-track score + link source + posting freshness, the at-a-glance line.
    ctx_bits: list[str] = []
    best = _best_track(state, job)
    if best:
        ctx_bits.append(f"Best track: **{best[0]} {best[1]}**")
    _badge = source_label(job.get("source"))  # ADR-099: exact source name
    if _badge:
        ctx_bits.append(_badge)
    age = format_posting_age(job.get("posted_at"))
    if age:
        ctx_bits.append(age + ("  ⚠️ may be stale" if _stale else ""))
    if ctx_bits:
        st.caption("  ·  ".join(ctx_bits))
    st.caption(f"Found `{_fmt_ts(job.get('found_at'))}`  ·  from search `{wf_id[:8]}…`")


def _exclude_control(wf_id: str, job_id: str, job: dict) -> None:
    """Exclude is a FILTER input, not a status (ADR-088 E): de-emphasized, labelled
    as hiding from future searches, with no complementary 'pursuing/saved' set."""
    excluded = _is_excluded(wf_id, job_id, job)
    if excluded:
        if st.button("♻ Un-hide", key="opp_unhide", help="Show this job in searches again."):
            _do(lambda: api.unexclude_job(job_id), "Un-hidden.")
    else:
        if st.button("🚫 Hide", key="opp_hide",
                     help="Hide this job from future searches and cross-run matches (a filter, not a status)."):
            _do(lambda: api.exclude_job(job_id), "Hidden from future searches.")


# ── Why it fits / gaps + score ────────────────────────────────────────────────

def _why_and_gaps(pipeline: dict, state: dict) -> None:
    score = pipeline.get("score")
    st.markdown("---")
    if not score:
        st.info("This job was not scored.")
        return
    sd = score.get("data") or {}

    # Score metrics: Overall + only the tracks that were actually scored (ADR-071).
    metrics = [("Overall", sd.get("overall_score"))]
    for label, key in (("Technical", "technical_score"),
                       ("Architecture", "architecture_score"),
                       ("Leadership", "leadership_score")):
        if sd.get(key) is not None:
            metrics.append((label, sd.get(key)))
    for col, (label, value) in zip(st.columns(len(metrics)), metrics):
        col.metric(label, value if value is not None else "—")

    why_col, gap_col = st.columns(2)
    with why_col:
        st.markdown("#### Why it fits")
        _para("Summary", sd.get("match_summary"))
        _bullets("Strengths", sd.get("strengths"))
        _para("Recommended", sd.get("recommended_next_action"))
    with gap_col:
        st.markdown("#### Gaps")
        # The richest gap split comes from career advice when present; fall back to
        # the score's flat gaps. Resume gaps CAN be tailored; career gaps must not be
        # fabricated (CLAUDE.md tailoring rules).
        adv = (pipeline.get("advice") or {}).get("data") or {}
        if adv.get("resume_gaps") or adv.get("career_gaps"):
            _bullets("Resume gaps (can tailor)", adv.get("resume_gaps"))
            _bullets("Career gaps (must not fabricate)", adv.get("career_gaps"))
        else:
            _bullets("Gaps", sd.get("gaps"))


# ── Deep review: run on demand, or show the rounds ────────────────────────────

def _deep_review(wf_id: str, job_id: str, pipeline: dict) -> None:
    rounds = pipeline.get("review_rounds") or []
    st.markdown("---")
    head_l, head_r = st.columns([3, 1])
    head_l.markdown("#### Deep review")
    if not rounds:
        head_r.caption("not run yet")
        st.caption("Run the critic + auditor reflection loop for a deeper read of fit and gaps.")
        if st.button(f"Run deep review  {_COST_HINT['deep_review']}", key="opp_run_review"):
            _run(lambda: api.trigger_deep_review(wf_id, job_id),
                 "Deep review (~20-40s)…", clears=[_cached_job_pipeline])
        return
    head_r.caption(f"done · {len(rounds)} round(s)")
    for r in rounds:
        with st.expander(
            f"Round {r['round_number']} — audit score {r.get('audit_score', '—')}  ·  "
            f"`{_fmt_ts(r['created_at'])}`",
            expanded=(r["round_number"] == len(rounds)),
        ):
            critic = r.get("critic") or {}
            audit = r.get("audit") or {}
            cc1, cc2 = st.columns(2)
            with cc1:
                st.markdown("##### Critic")
                _para("Fit summary", critic.get("overall_fit_summary"))
                _bullets("Critical gaps", critic.get("critical_gaps"))
                _bullets("Resume gaps (can tailor)", critic.get("resume_only_gaps"))
                _bullets("Career gaps", critic.get("career_gaps_observed"))
            with cc2:
                st.markdown("##### Auditor")
                _para("Quality summary", audit.get("quality_summary"))
                _bullets("Missing analysis", audit.get("missing_analysis_points"))
                _bullets("Recommended revisions", audit.get("recommended_revision_instructions"))


# ── Next steps: tailor + interview + the full tailoring flow ──────────────────

def _next_steps(wf_id: str, job_id: str, state: dict) -> None:
    st.markdown("---")
    st.markdown("#### Next steps")

    selected_ids = {
        (sj.get("job_id") or sj.get("id"))
        for sj in (state.get("selected_jobs") or [])
    }
    auto_selected = job_id in selected_ids

    if st.button(f"🎤 Prep for interview  {_COST_HINT['interview']}",
                 key="opp_prep", use_container_width=True):
        _run(lambda: api.trigger_interview_prep(wf_id, job_id),
             "Interview coach (~10-20s)…", clears=[_cached_job_pipeline], timeout_ok=True)

    # Tailoring: the shared per-job panel (ADR-090 extraction), reused by the Resume
    # Clinic's job focus so the flow lives in one place.
    render_job_tailoring(
        wf_id, job_id, resume_profile=state.get("resume_profile") or None,
        key_prefix="opp", trigger_label=f"✨ Tailor my resume  {_COST_HINT['tailor']}",
        on_demand_note=not auto_selected,
    )


# ── Interview prep (read) + collapsed extras ──────────────────────────────────

def _interview_prep_read(pipeline: dict) -> None:
    prep = pipeline.get("prep")
    if not prep:
        return
    d = prep.get("data") or {}
    st.markdown("---")
    st.markdown(f"#### Interview prep  ·  `{_fmt_ts(prep.get('created_at'))}`")
    _bullets("Likely interview topics", d.get("likely_interview_topics"))
    _bullets("Technical topics to review", d.get("technical_topics_to_review"))
    _bullets("Leadership stories to prepare", d.get("leadership_stories_to_prepare"))
    _bullets("Weak areas to defend", d.get("weak_areas_to_defend"))
    _bullets("Questions to ask the interviewer", d.get("questions_to_ask_interviewer"))
    _bullets("7-day prep plan", d.get("seven_day_prep_plan"))


def _more_detail(pipeline: dict) -> None:
    """Career advice + final review, collapsed - present for completeness without
    crowding the action surface (the mega-page risk the IA warns about)."""
    adv = pipeline.get("advice")
    fr = pipeline.get("final_review")
    if not adv and not fr:
        return
    st.markdown("---")
    with st.expander("More detail — career advice & final resume review", expanded=False):
        if adv:
            d = adv.get("data") or {}
            st.markdown(f"##### Career advice  ·  `{_fmt_ts(adv.get('created_at'))}`")
            _para("Positioning", d.get("positioning_summary"))
            _para("Recommended positioning", d.get("recommended_positioning"))
            _para("Recommended next action", d.get("recommended_next_action"))
            _bullets("Skills to strengthen", d.get("skills_to_strengthen"))
            _bullets("Experience to collect", d.get("experience_to_collect"))
            _bullets("30 / 60 / 90-day plan", d.get("thirty_sixty_ninety_day_plan"))
        if fr:
            d = fr.get("data") or {}
            st.markdown(f"##### Final resume review  ·  `{_fmt_ts(fr.get('created_at'))}`")
            _para("Fit summary", d.get("overall_fit_summary"))
            _bullets("Suggested improvements", d.get("suggested_improvements"))
            _bullets("Questions for you", d.get("questions_for_user"))


# ── Inline picker (navigated here with no job) ────────────────────────────────

def _picker() -> None:
    back_button("Matches")
    st.header("Open an opportunity")
    st.caption("Pick a search and a job to open.")
    runs = _cached_recent_workflows()
    if runs.empty:
        st.info("No searches found. Start one from **New search**.")
        return
    run_opts = {
        f"`{r['workflow_id']}`  ({int(r.get('jobs_scored', 0))} scored)": r["workflow_id"]
        for _, r in runs.iterrows()
    }
    run_label = st.selectbox("Search", list(run_opts.keys()), key="opp_wf_pick")
    picked_wf = run_opts[run_label]
    jobs = _cached_workflow_jobs(picked_wf)
    if jobs.empty:
        st.info("No scored jobs in this search yet.")
        return
    job_opts = {
        f"{r['title']} @ {r['company']}  ·  overall {int(r['overall_score'])}": r["job_id"]
        for _, r in jobs.iterrows()
    }
    job_label = st.selectbox("Job", list(job_opts.keys()), key="opp_job_pick")
    if st.button("Open ▶", key="opp_open"):
        _navigate("Opportunity", detail_workflow_id=picked_wf,
                  detail_job_id=job_opts[job_label])


# ── Small helpers ─────────────────────────────────────────────────────────────

def _best_track(state: dict, job: dict) -> tuple[str, int] | None:
    """The highest-scoring ACTIVE track for this job (ADR-071), as (label, score)."""
    try:
        active = get_active_tracks(state)
    except Exception:  # noqa: BLE001
        active = []
    best: tuple[str, int] | None = None
    for t in active:
        score_key = TRACK_TO_SCORE_KEY.get(t)
        val = job.get(score_key) if score_key else None
        if val is None:
            continue
        try:
            ival = int(val)
        except (TypeError, ValueError):
            continue
        if best is None or ival > best[1]:
            best = (_TRACK_LABEL.get(t, t), ival)
    return best


def _is_excluded(wf_id: str, job_id: str, job: dict) -> bool:
    if job.get("excluded") is not None:
        return bool(job.get("excluded"))
    try:
        df = _cached_workflow_jobs(wf_id)
        if not df.empty and "excluded" in df.columns:
            row = df[df["job_id"] == job_id]
            if not row.empty:
                return bool(row.iloc[0]["excluded"] or 0)
    except Exception:  # noqa: BLE001
        pass
    return False


def _do(action, success_msg: str) -> None:
    """Run a quick write, refresh caches, rerun."""
    try:
        action()
        st.cache_data.clear()
        st.success(success_msg)
        st.rerun()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Action failed: {exc}")


def _run(action, spinner: str, *, clears=(), timeout_ok: bool = False) -> None:
    """Run a slow agent action with a spinner; clear the named caches; rerun.

    When ``timeout_ok`` the synchronous server path may outlast the socket timeout
    (the work usually still persists), so a ReadTimeout becomes a soft warning, not
    an error (same contract as the old Workflow Detail buttons)."""
    with st.spinner(spinner):
        try:
            action()
        except httpx.ReadTimeout:
            for c in clears:
                c.clear()
            if timeout_ok:
                st.warning("Client timed out, but the server may have finished. "
                           "Reload to see the result.")
                return
            raise
        except Exception as exc:  # noqa: BLE001
            st.error(f"Failed: {exc}")
            return
    for c in clears:
        c.clear()
    st.rerun()
