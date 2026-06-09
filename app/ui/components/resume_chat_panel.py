"""Shared resume chat-revise + export panel (ADR-068 / ADR-072).

Extracted verbatim from app/ui/views/resume_clinic.py so the SAME panel backs:
  - the Resume Clinic view (job-agnostic), and
  - the tailoring card's "Open live chat" (job-seeded; ADR-072).

The panel is the "Refine with feedback" chat loop (live preview + session-cost
meter + chat input + send / save-final-edit / discard + conversation log) plus the
"Export the final resume" block. It operates on a clinic-review/session dict
(`review`) and refreshes the current state into `st.session_state[state_key]` so a
host view can keep its own handle. Per-session widget/cost/history keys are keyed
by `clinic_id`, so two hosts never collide.

Behavior is identical to the original clinic implementation; only the
session-state key for "the current review" is parameterized (`state_key`).
"""
from __future__ import annotations

import httpx
import streamlit as st

import app.ui.api_client as api
from app.services.resume_text_renderer import compose_resume, render_markdown


def render_chat_panel(review: dict, *, user_id: str,
                      state_key: str = "rc_last_review") -> None:
    """Render the chat-revise + export panel for `review`.

    review     : the clinic-review/session dict (must carry clinic_id, resume_id,
                 overhaul, edited, decision).
    user_id    : owning profile, used to refresh the row after a chat turn.
    state_key  : session-state key holding "the current review"; refreshes are
                 written here so the host view re-renders with the latest state.
    """
    # ── Refine with feedback (ADR-068) ───────────────────────────────────────
    st.markdown("---")
    st.subheader("Refine with feedback")
    st.caption(
        "Iteratively revise the overhaul through chat. Each turn updates "
        "the preview in place; click **Save final edit** when you're done "
        "to lock the result in as your final draft, or **Discard chat edits** "
        "to revert to the agent's original overhaul."
    )

    # Live preview of the current state. compose_resume reads the edited overhaul
    # whenever populated (per ADR-068), so the preview reflects the latest chat
    # turn automatically.
    _rc_preview_review = st.session_state.get(state_key) or review or {}
    _rc_overhaul = _rc_preview_review.get("overhaul")
    _rc_edited = _rc_preview_review.get("edited")
    _rc_decision = _rc_preview_review.get("decision")
    _rc_resume_id = _rc_preview_review.get("resume_id")
    _clinic_id = _rc_preview_review.get("clinic_id") or review.get("clinic_id")

    # Fetch the parsed profile via the API (ADR-075 Phase 8) so we render against
    # the same data the backend chat agent saw, without the UI opening the DB.
    # Falls back to an empty dict if it can't be loaded (the chat still works but
    # the preview may be sparse).
    _rc_profile_dict: dict = {}
    if _rc_resume_id:
        try:
            _rc_profile_dict = api.get_resume_profile(user_id, _rc_resume_id)
        except Exception:
            _rc_profile_dict = {}

    try:
        _rc_rendered = compose_resume(
            _rc_profile_dict, _rc_overhaul, _rc_edited, _rc_decision,
        )
        _rc_markdown = render_markdown(_rc_rendered)
    except Exception as _e:
        _rc_markdown = f"_Preview unavailable: {_e}_"

    with st.expander("Live preview", expanded=True):
        st.markdown(_rc_markdown)

    # ── Session cost meter (ADR-068) ────────────────────────────────────────
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

    # ── Fidelity verdict (ADR-091) ──────────────────────────────────────────
    # Surface the reviewer's latest verdict so the user sees WHY a draft is
    # flagged. The next chat turn feeds these claims back to the agent, which
    # tries to fix them automatically (the user can also direct it).
    _rc_fid = _rc_preview_review.get("fidelity_review") or {}
    if _rc_fid:
        _fid_status = (_rc_fid.get("overall_fidelity_status") or "").lower()
        _fid_claims = _rc_fid.get("unsupported_claims") or []
        if _fid_status == "pass" and not _fid_claims:
            st.success("Fidelity check passed - every rewrite is backed by your resume.")
        else:
            _fid_label = {"fail": "failed",
                          "needs_revision": "needs revision"}.get(
                              _fid_status, _fid_status or "flagged")
            with st.expander(
                f"Fidelity check: {_fid_label} "
                f"({len(_fid_claims)} unsupported claim(s))",
                expanded=True,
            ):
                if _fid_claims:
                    st.caption(
                        "The reviewer flagged these as not backed by your "
                        "resume. Your next chat turn will automatically try to "
                        "ground or remove them - or tell it how you'd like them "
                        "handled."
                    )
                    for _c in _fid_claims:
                        st.markdown(f"- {_c}")
                else:
                    st.caption("The reviewer recommends another revision pass.")

    # ── Chat input ──────────────────────────────────────────────────────────
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
    _rc_msg_key = f"rc_chat_msg_{_clinic_id}"
    # Clear the box after a successful send. The send handler sets a pending-
    # clear flag and reruns; we honor it HERE, before the text_area is
    # instantiated, because Streamlit forbids mutating a widget's value once it
    # exists in the same run.
    if st.session_state.pop(f"{_rc_msg_key}__clear", False):
        st.session_state[_rc_msg_key] = ""
    _rc_message = st.text_area(
        "What would you like to change?",
        placeholder=(
            "e.g. \"make the summary shorter and front-load the "
            "cybersecurity angle\" or \"promote my projects above experience\""
        ),
        key=_rc_msg_key,
        height=80,
    )

    _rc_chat_history_key = f"rc_chat_history_{_clinic_id}"
    if _rc_chat_history_key not in st.session_state:
        st.session_state[_rc_chat_history_key] = []

    _cc1, _cc2, _cc3 = st.columns([2, 2, 2])
    # Always-enabled Send (no `disabled=` gate): a Streamlit text_area only
    # commits its value on blur/Ctrl+Enter, so a disabled-while-empty button
    # never re-enabled as the user typed. We validate on click instead.
    _send_clicked = _cc1.button(
        "Send feedback", type="primary",
        key=f"rc_chat_send_{_clinic_id}",
        use_container_width=True,
    )
    if _send_clicked and not _rc_message.strip():
        st.warning("Type what you'd like to change first.")
    elif _send_clicked:
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
                {"role": "assistant", "message": _chat_resp.get("reply") or ""},
            )
            # Stash the cost meter fields so they survive the rerun.
            st.session_state[_rc_cost_key] = {
                "turns_used":      _chat_resp.get("turns_used", 0),
                "max_turns":       _chat_resp.get("max_turns", 0),
                "session_cost_usd": _chat_resp.get("session_cost_usd", 0.0),
            }
            # Refresh the row so the preview re-renders. Build the updated state
            # FROM THE CHAT RESPONSE (which carries the new overhaul + fidelity
            # verdict) rather than re-fetching via list_resume_clinic_runs:
            # that list endpoint excludes job-anchored sessions (WHERE job_id IS
            # NULL, ADR-072), so for a job-focused chat the re-fetch was a silent
            # no-op - the preview never updated and "Save final edit" later
            # clobbered the accumulated chat edits with the original overhaul.
            # The chat turn writes edited_json server-side, so mirror that here:
            # the response overhaul IS the new edited state; decision is unchanged
            # (chat never decides).
            _base_review = st.session_state.get(state_key) or review or {}
            _merged = dict(_base_review)
            _merged["edited"] = _chat_resp.get("overhaul")
            _merged["fidelity_review"] = _chat_resp.get("fidelity_review")
            _merged["decision"] = None
            st.session_state[state_key] = _merged
            # Clear the input box on the next run (honored before the widget is
            # rebuilt) so it's ready for the next message.
            st.session_state[f"{_rc_msg_key}__clear"] = True
            st.rerun()
        except httpx.HTTPStatusError as exc:
            # Surface the cap-reached reason directly on 429.
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
            st.session_state[state_key] = _updated
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
            # Mirror the server-side clear locally. discard_edits nulls
            # edited_json/decision so the renderer falls back to the agent's
            # original overhaul; reflect that in the held state directly rather
            # than re-fetching via list_resume_clinic_runs (which excludes
            # job-anchored sessions, ADR-072, and would leave stale edits shown).
            _base_review = st.session_state.get(state_key) or review or {}
            _merged = dict(_base_review)
            _merged["edited"] = None
            _merged["fidelity_review"] = None
            _merged["decision"] = None
            st.session_state[state_key] = _merged
            st.session_state[_rc_chat_history_key] = []
            # Discard only clears edits on the server; the chat-turn spend on the
            # workflow_run_id is permanent (already billed in llm_calls). Leave
            # the meter intact so the user sees the true session cost.
            st.info("Chat edits discarded.")
            st.rerun()
        except Exception as exc:
            st.error(f"Discard failed: {exc}")

    # ── Conversation log ──────────────────────────────────────────────────────
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

    # ── Export the final resume ──────────────────────────────────────────────
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
        key=f"rc_export_format_{_clinic_id}",
    )
    try:
        _bytes, _ctype, _fname = api.export_resume_clinic(_clinic_id, _fmt)
    except Exception as exc:
        _bytes, _ctype, _fname = None, None, None
        st.error(f"Could not generate export: {exc}")
    if _bytes is not None:
        # Inline preview for the text-y formats.
        if _fmt in ("md", "txt"):
            with st.expander("Preview", expanded=False):
                st.code(_bytes.decode("utf-8", errors="replace"),
                        language=("markdown" if _fmt == "md" else None))
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
