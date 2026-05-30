"""Resume Clinic view - standalone, job-agnostic resume review (ADR-066/068).

Phase 4 of the UI refactor (docs/architecture/ui_refactor_plan.md). Extracted
verbatim into render(ctx); all st.* calls run inside render().
"""
from __future__ import annotations

import json

import streamlit as st

import app.ui.api_client as api
from app.ui.data import _get_config_cached
from app.ui.db_reader import load_user_clinic_reviews, load_user_resumes
from app.ui.formatting import _fmt_ts
from app.ui.nav import ViewContext


def render(ctx: ViewContext) -> None:
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
