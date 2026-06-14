"""Resume Clinic view - standalone, job-agnostic resume review (ADR-066/068).

Phase 4 of the UI refactor (docs/architecture/ui_refactor_plan.md). Extracted
verbatim into render(ctx); all st.* calls run inside render().
"""
from __future__ import annotations


import streamlit as st

import app.ui.api_client as api
from app.ui.components.resume_chat_panel import render_chat_panel
from app.ui.components.tailoring_panel import render_job_tailoring
from app.ui.data import _cached_favorites, _cached_user_resumes, _get_config_cached
from app.ui.formatting import _fmt_ts
from app.ui.nav import ViewContext


def render(ctx: ViewContext) -> None:
    st.header("Resume Clinic")
    st.caption(
        "Improve your resume. With no job focus it is a job-agnostic review; pick one "
        "of My favorite jobs to focus the session and produce a resume tailored to "
        "that role (ADR-090)."
    )

    user_id = st.session_state.current_user_id

    # ── ADR-090: optional job focus (from My favorite jobs) ───────────────────
    # A focus turns the session into a job-specific tailoring (the existing
    # evidence-bound engine), output = a tailored resume. No focus -> the standard
    # job-agnostic clinic below.
    # A job arriving from the Run Report / Matches "Analyze in clinic" jump (ADR-090):
    # preselect it in the focus picker below. One-shot hint (popped, not sticky).
    _focus_hint = st.session_state.pop("clinic_focus_job_id", None)
    _favs = _cached_favorites(user_id)
    if _favs:
        _focus_opts: dict[str, dict | None] = {"— No focus (improve my resume generally) —": None}
        for _f in _favs:
            _focus_opts[f"{_f.get('title') or 'Untitled'} @ {_f.get('company') or '?'}"] = _f
        if _focus_hint:
            for _lbl, _f in _focus_opts.items():
                if _f and str(_f.get("job_id")) == str(_focus_hint):
                    st.session_state.rc_focus_job = _lbl  # preselect the arrived-with job
                    break
        _focus_label = st.selectbox(
            "Focus a job (optional — from My favorite jobs)",
            list(_focus_opts.keys()), key="rc_focus_job",
            help="Pick a favorite to tailor your resume for that specific role and export it.",
        )
        _focused = _focus_opts.get(_focus_label)
        if _focused:
            st.subheader(
                f"Tailoring for {_focused.get('title') or 'this role'} "
                f"@ {_focused.get('company') or '?'}"
            )
            st.caption(
                "Output is a resume tailored to this job. Generate a draft, refine it "
                "in live chat, then export — same evidence-bound flow as the "
                "Opportunity page."
            )
            render_job_tailoring(
                str(_focused.get("workflow_id")), str(_focused.get("job_id")),
                resume_profile=None, key_prefix="clinic_focus",
                trigger_label="✨ Tailor my resume for this job",
                on_demand_note=True,
            )
            return
    else:
        st.caption("Tip: favorite a job from Matches to tailor your resume for it here.")

    # ── Resume picker (active resume preselected) ────────────────────────────
    # ADR-075 Phase 2: resumes via the API (GET /users/{id}/resumes) as a list of
    # dicts (active first), instead of db_reader.load_user_resumes.
    resumes = _cached_user_resumes(user_id).get("items") or []
    if not resumes:
        st.warning(
            "No resumes found for this profile. Upload one in Profiles, then return here."
        )
        st.stop()

    resume_label_by_id: dict[str, str] = {}
    for _row in resumes:
        _flag = " (active)" if int(_row.get("is_active") or 0) else ""
        resume_label_by_id[str(_row["resume_id"])] = (
            f"{_row.get('file_name') or _row['resume_id']}  ·  v{_row.get('version') or '?'}{_flag}"
        )

    _active = next((r for r in resumes if int(r.get("is_active") or 0)), None)
    default_resume_id = str((_active or resumes[0])["resume_id"])

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

            # ── Refine with feedback + export (ADR-068; shared panel, ADR-072) ──
            render_chat_panel(review, user_id=user_id, state_key="rc_last_review")

    # ── Past runs ────────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Past clinic runs")
    # ADR-075 Phase 2: reuse GET /users/{id}/resume-clinic (list_by_user, now
    # filtered to job_id IS NULL per ADR-072) instead of db_reader.
    try:
        past = api.list_resume_clinic_runs(user_id).get("reviews") or []
    except Exception:
        past = []
    if not past:
        st.caption("No past clinic runs for this profile yet.")
    else:
        for _row in past:
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
