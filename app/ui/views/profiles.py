"""Profiles view - manage profiles + the onboarding wizard (ADR-062).

Phase 4 of the UI refactor (docs/architecture/ui_refactor_plan.md). Extracted
verbatim into render(ctx); all st.* calls run inside render().
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import app.ui.api_client as api
from app.ui.data import _cached_list_users
from app.ui.db_reader import load_user_resumes
from app.ui.formatting import _fmt_ts
from app.ui.nav import ViewContext


def render(ctx: ViewContext) -> None:
    st.header("Profiles")
    st.caption(
        "A profile is one job-seeker served from this install. Each has its own "
        "resume, search defaults, config, memory, cost view, and history. There "
        "is no login — switching profiles in the sidebar re-scopes what you see."
    )

    # Existing profiles
    _users = _cached_list_users()
    if _users:
        st.subheader("Existing profiles")
        st.dataframe(
            pd.DataFrame([
                {"ID": u["id"], "Name": u["name"], "Note": u.get("note") or "",
                 "Created": _fmt_ts(u.get("created_at"))}
                for u in _users
            ]),
            hide_index=True,
            use_container_width=True,
        )

        # ── Manage an existing profile ────────────────────────────────────────
        st.subheader("Manage an existing profile")
        _opts = {str(u["id"]): f"{u['name']}  (#{u['id']})" for u in _users}
        _by_id = {str(u["id"]): u for u in _users}

        with st.expander("Edit a profile (name / note)"):
            _eid = st.selectbox(
                "Profile to edit", list(_opts.keys()),
                format_func=lambda i: _opts.get(i, i), key="edit_profile_select",
            )
            _cur = _by_id.get(_eid, {})
            _new_name = st.text_input("Display name", value=_cur.get("name") or "",
                                      key=f"edit_name_{_eid}")
            _new_note = st.text_input("Note (optional)", value=_cur.get("note") or "",
                                      key=f"edit_note_{_eid}")
            if st.button("Save changes", key=f"edit_save_{_eid}"):
                if not _new_name.strip():
                    st.error("Display name is required.")
                else:
                    try:
                        api.update_user(_eid, _new_name.strip(), _new_note.strip() or None)
                        _cached_list_users.clear()
                        st.success(f"Updated profile #{_eid}.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not update profile: {exc}")

        with st.expander("Add a resume to a profile"):
            _rid = st.selectbox(
                "Profile", list(_opts.keys()),
                format_func=lambda i: _opts.get(i, i), key="add_resume_select",
            )
            _rfile = st.file_uploader("Resume PDF", type=["pdf"],
                                      key=f"add_resume_file_{_rid}")
            if st.button("Upload resume", type="primary",
                         disabled=_rfile is None, key=f"add_resume_btn_{_rid}"):
                try:
                    with st.spinner("Parsing resume (this can take a moment)…"):
                        resp = api.upload_resume(_rid, _rfile.getvalue(), _rfile.name)
                    st.success(f"Stored resume `{resp.get('resume_id')}` as the active "
                               f"resume for profile #{_rid}.")
                    st.cache_data.clear()
                except Exception as exc:
                    st.error(f"Resume upload failed: {exc}")

        with st.expander("Delete a resume from a profile"):
            st.caption(
                "Removes a resume from the profile and cascades to its Resume "
                "Clinic reviews (the past-runs panel would otherwise show "
                "broken rows). Job-search workflow history is preserved. "
                "Re-upload to get a fresh parse under the latest parser prompt."
            )
            _dprof = st.selectbox(
                "Profile", list(_opts.keys()),
                format_func=lambda i: _opts.get(i, i), key="del_resume_profile",
            )
            _dprof_resumes = load_user_resumes(_dprof)
            if _dprof_resumes.empty:
                st.info("This profile has no resumes to delete.")
            else:
                _del_options = {}
                for _, _r in _dprof_resumes.iterrows():
                    _flag = " (active)" if int(_r.get("is_active") or 0) else ""
                    _del_options[str(_r["resume_id"])] = (
                        f"{_r.get('file_name') or _r['resume_id']}  ·  "
                        f"v{_r.get('version') or '?'}{_flag}  ·  "
                        f"{_fmt_ts(_r.get('created_at'))}"
                    )
                _dres = st.selectbox(
                    "Resume to delete",
                    options=list(_del_options.keys()),
                    format_func=lambda i: _del_options[i],
                    key=f"del_resume_select_{_dprof}",
                )
                # Count clinic reviews that would cascade so the user sees the
                # full impact before confirming.
                try:
                    _clinic_rows = api.list_resume_clinic_runs(_dprof).get("reviews") or []
                    _cascade_count = sum(
                        1 for r in _clinic_rows if r.get("resume_id") == _dres
                    )
                except Exception:
                    _cascade_count = 0
                _confirm = st.checkbox(
                    f"Yes — also delete this resume's **{_cascade_count}** Resume "
                    f"Clinic review(s). Job-search history stays.",
                    key=f"del_resume_confirm_{_dprof}_{_dres}",
                )
                if st.button(
                    "Delete resume",
                    type="primary",
                    disabled=not _confirm,
                    key=f"del_resume_btn_{_dprof}_{_dres}",
                ):
                    try:
                        resp = api.delete_resume(_dprof, _dres)
                        st.success(
                            f"Deleted resume `{_dres[:8]}…` and "
                            f"{resp.get('clinic_reviews_deleted', 0)} clinic review(s)."
                        )
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Delete failed: {exc}")

    st.markdown("---")
    st.subheader("Add a profile")

    step = st.session_state.onboard_step

    # ── Step 1: identity ──────────────────────────────────────────────────────
    if step == 1:
        st.markdown("**Step 1 of 3 — Identity**")
        with st.form("onboard_identity"):
            name = st.text_input("Display name", placeholder="e.g. Alex (son)")
            note = st.text_input(
                "Note (optional)",
                placeholder="e.g. New-grad SWE, west coast — human-only label",
                help="Descriptive only. The system never acts on this.",
            )
            go = st.form_submit_button("Create profile", type="primary")
        if go:
            if not name.strip():
                st.error("A display name is required.")
            else:
                try:
                    resp = api.create_user(name.strip(), note.strip() or None)
                    new = resp.get("user") or {}
                    st.session_state.onboard_new_user_id = new.get("id")
                    st.session_state.onboard_step = 2
                    _cached_list_users.clear()
                    st.success(f"Created profile #{new.get('id')} — {new.get('name')}.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not create profile: {exc}")

    # ── Step 2: resume ────────────────────────────────────────────────────────
    elif step == 2:
        new_uid = st.session_state.onboard_new_user_id
        st.markdown(f"**Step 2 of 3 — Resume** (profile #{new_uid})")
        st.caption("Upload a PDF resume for this profile. It becomes the profile's "
                   "active resume. You can skip and add one later.")
        up = st.file_uploader("Resume PDF", type=["pdf"], key="onboard_resume_file")
        c1, c2 = st.columns(2)
        if c1.button("Upload and continue", type="primary", disabled=up is None):
            try:
                with st.spinner("Parsing resume (this can take a moment)…"):
                    resp = api.upload_resume(new_uid, up.getvalue(), up.name)
                st.success(f"Stored resume `{resp.get('resume_id')}` for "
                           f"{resp.get('name') or 'this profile'}.")
                st.session_state.onboard_step = 3
                st.rerun()
            except Exception as exc:
                st.error(f"Resume upload failed: {exc}")
        if c2.button("Skip for now"):
            st.session_state.onboard_step = 3
            st.rerun()

    # ── Step 3: default search criteria ───────────────────────────────────────
    elif step == 3:
        new_uid = st.session_state.onboard_new_user_id
        st.markdown(f"**Step 3 of 3 — Default search criteria** (profile #{new_uid})")
        st.caption("Saved as this profile's defaults; Start New Run pre-fills from "
                   "them. Skippable — you can set them later in Settings.")
        with st.form("onboard_search"):
            roles = st.text_input("Roles (comma-separated)",
                                  placeholder="Security Analyst, SOC Analyst")
            locations = st.text_area("Locations (one per line)",
                                     placeholder="Atlanta, GA\nRemote", height=90,
                                     help="One per line so 'City, State' stays intact.")
            cs1, cs2 = st.columns(2)
            save = cs1.form_submit_button("Save and finish", type="primary")
            skip = cs2.form_submit_button("Skip and finish")
        if save or skip:
            if save:
                # Persist as the NEW profile's user_config defaults. Temporarily
                # point the client at that profile so the writes are owned by it,
                # then restore the active profile.
                _prev = st.session_state.current_user_id
                try:
                    api.set_user_id(str(new_uid))
                    role_list = [r.strip() for r in roles.split(",") if r.strip()]
                    loc_list = [l.strip() for l in locations.splitlines() if l.strip()]
                    if role_list:
                        api.put_config("search.titles", role_list)
                    if loc_list:
                        api.put_config("search.locations", loc_list)
                except Exception as exc:
                    st.warning(f"Could not save search defaults: {exc}")
                finally:
                    api.set_user_id(_prev)
            st.session_state.onboard_step = 1
            st.success(f"Profile #{new_uid} is ready. Select it in the sidebar to use it.")
            _cached_list_users.clear()

    if step != 1:
        if st.button("Cancel / start over"):
            st.session_state.onboard_step = 1
            st.session_state.onboard_new_user_id = None
            st.rerun()
