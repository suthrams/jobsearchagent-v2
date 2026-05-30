"""Job Detail view - per-job drill-down across the whole pipeline.

Phase 3 of the UI refactor (docs/architecture/ui_refactor_plan.md). Extracted
verbatim into render(ctx); all st.* calls run inside render().
"""
from __future__ import annotations

import streamlit as st

from app.ui.components.bullets import _bullets, _para
from app.ui.db_reader import load_job_pipeline, load_recent_workflows, load_workflow_jobs
from app.ui.formatting import _fmt_ts
from app.ui.nav import ViewContext, _navigate


def render(ctx: ViewContext) -> None:
    st.header("Job Detail")

    wf_id = st.session_state.detail_workflow_id
    job_id = st.session_state.detail_job_id

    # ── Inline picker when navigated here directly ────────────────────────────
    if not wf_id or not job_id:
        st.caption("Pick a workflow run and job to drill into.")
        _all_runs = load_recent_workflows()
        if _all_runs.empty:
            st.info("No workflow runs found. Start one from **Start New Run**.")
            st.stop()

        _run_options = {
            f"`{r['workflow_id']}`  ({int(r.get('jobs_scored', 0))} scored)": r["workflow_id"]
            for _, r in _all_runs.iterrows()
        }
        _run_label = st.selectbox("Workflow run", list(_run_options.keys()), key="jd_wf_pick")
        _picked_wf = _run_options[_run_label]

        _jobs_pick = load_workflow_jobs(_picked_wf)
        if _jobs_pick.empty:
            st.info("No scored jobs in this run yet.")
            st.stop()

        _job_options = {
            f"{r['title']} @ {r['company']}  ·  overall {int(r['overall_score'])}": r["job_id"]
            for _, r in _jobs_pick.iterrows()
        }
        _job_label = st.selectbox("Job", list(_job_options.keys()), key="jd_job_pick")
        if st.button("Open ▶", key="jd_open"):
            _navigate("Job Detail",
                      detail_workflow_id=_picked_wf,
                      detail_job_id=_job_options[_job_label])
        st.stop()

    # Top breadcrumb / back nav
    nav1, nav2 = st.columns([1, 1])
    if nav1.button("← Back to Workflow Detail"):
        _navigate("Workflow Detail")
    if nav2.button("Back to Workflow History"):
        _navigate("Workflow History")

    pipeline = load_job_pipeline(wf_id, job_id)
    job = pipeline["job"] or {}

    # ── Job header ────────────────────────────────────────────────────────────
    if not job:
        st.error(f"No job row found for `{job_id}`. The job may have been purged or never persisted.")
        st.stop()

    st.markdown(f"### {job.get('title') or '(no title)'}")
    st.markdown(
        f"**{job.get('company') or '—'}**  ·  {job.get('location') or '—'}  ·  "
        f"source `{job.get('source') or '—'}`"
    )
    if job.get("url"):
        st.markdown(f"[Open posting ↗]({job['url']})")
    st.caption(
        f"Workflow `{wf_id}`  ·  job_id `{job_id}`  ·  "
        f"first found `{_fmt_ts(job.get('found_at'))}`"
    )

    # ── Build a chronological timeline of all this job's stages ────────────
    timeline_rows: list[dict] = []
    if job.get("found_at"):
        timeline_rows.append({"ts": job["found_at"], "stage": "Discovered"})
    if pipeline.get("score"):
        timeline_rows.append({"ts": pipeline["score"]["created_at"], "stage": "Scored"})
    for r in pipeline.get("review_rounds") or []:
        timeline_rows.append({
            "ts": r["created_at"],
            "stage": f"Review round {r['round_number']} (audit {r.get('audit_score', '—')})",
        })
    if pipeline.get("final_review"):
        timeline_rows.append({"ts": pipeline["final_review"]["created_at"],
                              "stage": "Final review persisted"})
    if pipeline.get("advice"):
        timeline_rows.append({"ts": pipeline["advice"]["created_at"], "stage": "Career advice"})
    if pipeline.get("prep"):
        timeline_rows.append({"ts": pipeline["prep"]["created_at"], "stage": "Interview prep"})
    timeline_rows.sort(key=lambda r: r["ts"] or "")

    if timeline_rows:
        st.markdown("---")
        st.subheader("Timeline for this job")
        _bullets(
            "",
            [f"`{_fmt_ts(r['ts'])}` — {r['stage']}" for r in timeline_rows],
        )

    # ── Score ─────────────────────────────────────────────────────────────────
    score = pipeline.get("score")
    st.markdown("---")
    if score:
        st.subheader(f"Score — produced `{_fmt_ts(score['created_at'])}`")
        sd = score["data"] or {}
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Overall",      sd.get("overall_score", "—"))
        s2.metric("Technical",    sd.get("technical_score", "—"))
        s3.metric("Architecture", sd.get("architecture_score", "—"))
        s4.metric("Leadership",   sd.get("leadership_score", "—"))
        st.markdown("")
        _para("Summary",     sd.get("match_summary"))
        _para("Recommended", sd.get("recommended_next_action"))
        _bullets("Strengths", sd.get("strengths"))
        _bullets("Gaps",      sd.get("gaps"))
    else:
        st.subheader("Score")
        st.info("This job was not scored.")

    # ── Review rounds (chronological) ────────────────────────────────────────
    rounds = pipeline.get("review_rounds") or []
    if rounds:
        st.markdown("---")
        st.subheader(f"Deep Review — {len(rounds)} round(s)")
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
                    st.markdown("#### Critic")
                    _para("Fit summary",                 critic.get("overall_fit_summary"))
                    _bullets("Critical gaps",            critic.get("critical_gaps"))
                    _bullets("Resume gaps (can tailor)", critic.get("resume_only_gaps"))
                    _bullets("Career gaps",              critic.get("career_gaps_observed"))
                with cc2:
                    st.markdown("#### Auditor")
                    _para("Quality summary",          audit.get("quality_summary"))
                    _bullets("Missing analysis",      audit.get("missing_analysis_points"))
                    _bullets("Recommended revisions", audit.get("recommended_revision_instructions"))
                    if r.get("stop_reason"):
                        st.caption(f"Stop reason: {r['stop_reason']}")

    # ── Final resume review (the persisted "best" one) ────────────────────────
    fr = pipeline.get("final_review")
    if fr:
        st.markdown("---")
        st.subheader(f"Final Resume Review — persisted `{_fmt_ts(fr['created_at'])}`")
        d = fr["data"] or {}
        _para("Fit summary",            d.get("overall_fit_summary"))
        _bullets("Suggested improvements", d.get("suggested_improvements"))
        _bullets("Questions for you",      d.get("questions_for_user"))

    # ── Career advice ─────────────────────────────────────────────────────────
    adv = pipeline.get("advice")
    if adv:
        st.markdown("---")
        st.subheader(f"Career Advice — produced `{_fmt_ts(adv['created_at'])}`")
        d = adv["data"] or {}
        _para("Positioning",                d.get("positioning_summary"))
        _para("Recommended positioning",    d.get("recommended_positioning"))
        _para("Recommended next action",    d.get("recommended_next_action"))
        _bullets("Resume gaps (can address through tailoring)", d.get("resume_gaps"))
        _bullets("Career gaps (must not fabricate)",            d.get("career_gaps"))
        _bullets("Skills to strengthen",                        d.get("skills_to_strengthen"))
        _bullets("Experience to collect",                       d.get("experience_to_collect"))
        _bullets("30 / 60 / 90-day plan",                       d.get("thirty_sixty_ninety_day_plan"))

    # ── Interview prep ────────────────────────────────────────────────────────
    prep = pipeline.get("prep")
    if prep:
        st.markdown("---")
        st.subheader(f"Interview Prep — produced `{_fmt_ts(prep['created_at'])}`")
        d = prep["data"] or {}
        _bullets("Likely interview topics",       d.get("likely_interview_topics"))
        _bullets("Technical topics to review",    d.get("technical_topics_to_review"))
        _bullets("Leadership stories to prepare", d.get("leadership_stories_to_prepare"))
        _bullets("Weak areas to defend",          d.get("weak_areas_to_defend"))
        _bullets("Questions to ask the interviewer", d.get("questions_to_ask_interviewer"))
        _bullets("7-day prep plan",               d.get("seven_day_prep_plan"))
