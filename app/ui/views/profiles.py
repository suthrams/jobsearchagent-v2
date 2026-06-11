"""Profiles view - manage job-seeker profiles + their resumes (ADR-062).

A usable, table-driven manager: every profile in one row with its resume status
and an actions column (edit / upload-overwrite resume / delete), plus an "Add New"
flow that creates a profile and uploads its resume in one go. All per-row actions
open st.dialog modals. No backend beyond the existing /users endpoints + the
DELETE /users/{id} added for profile deletion.

All st.* calls (incl. the @st.dialog decorations) run inside render(ctx), so
importing this module executes no Streamlit call (the structure tests rely on that).
"""
from __future__ import annotations

import streamlit as st

import app.ui.api_client as api
from app.ui.data import _cached_list_users, _cached_user_resumes
from app.ui.formatting import _fmt_ts
from app.ui.nav import ViewContext


def _active_resume(uid: str) -> dict | None:
    """The profile's active resume row (or the newest), or None."""
    items = (_cached_user_resumes(uid).get("items") or [])
    if not items:
        return None
    for r in items:
        if int(r.get("is_active") or 0):
            return r
    return items[0]


def render(ctx: ViewContext) -> None:
    st.header("Profiles")
    st.caption(
        "A profile is one job seeker served from this install - its own resume, "
        "search defaults, config, memory, cost view, and history. There is no login; "
        "switching profiles in the sidebar re-scopes what you see."
    )

    users = _cached_list_users()
    active_uid = str(st.session_state.get("current_user_id", "0"))

    # ── Dialogs (defined inside render so import stays st-free) ────────────────
    @st.dialog("Add a new profile")
    def _add_new():
        st.caption("Create the profile and (optionally) upload its resume in one go.")
        name = st.text_input("Display name", placeholder="e.g. Alex (son)")
        note = st.text_input("Note (optional)", placeholder="Human-only label; never acted on")
        pdf = st.file_uploader("Resume PDF (optional)", type=["pdf"])
        if st.button("Create profile", type="primary"):
            if not name.strip():
                st.error("A display name is required.")
                return
            try:
                resp = api.create_user(name.strip(), note.strip() or None)
                new_id = (resp.get("user") or {}).get("id")
                if pdf is not None:
                    with st.spinner("Parsing resume (this can take a moment)..."):
                        api.upload_resume(new_id, pdf.getvalue(), pdf.name)
                st.cache_data.clear()
                st.success(f"Created profile #{new_id}"
                           + (" with its resume." if pdf is not None else "."))
                st.rerun()
            except Exception as exc:  # noqa: BLE001 - surface, never crash
                st.error(f"Could not create profile: {exc}")

    @st.dialog("Edit profile")
    def _edit(u: dict):
        name = st.text_input("Display name", value=u.get("name") or "")
        note = st.text_input("Note (optional)", value=u.get("note") or "")
        if st.button("Save changes", type="primary"):
            if not name.strip():
                st.error("Display name is required.")
                return
            try:
                api.update_user(u["id"], name.strip(), note.strip() or None)
                _cached_list_users.clear()
                st.success(f"Updated profile #{u['id']}.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not update profile: {exc}")

    @st.dialog("Upload / overwrite resume")
    def _upload(u: dict):
        st.caption(f"Upload a PDF resume for **{u.get('name')}** (#{u['id']}). It "
                   "becomes the profile's active resume.")
        pdf = st.file_uploader("Resume PDF", type=["pdf"])
        if st.button("Upload", type="primary", disabled=pdf is None):
            try:
                with st.spinner("Parsing resume (this can take a moment)..."):
                    resp = api.upload_resume(u["id"], pdf.getvalue(), pdf.name)
                st.cache_data.clear()
                st.success(f"Stored resume `{resp.get('resume_id', '')[:8]}` as the "
                           f"active resume for #{u['id']}.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Resume upload failed: {exc}")

    @st.dialog("View resume")
    def _view(u: dict):
        r = _active_resume(str(u["id"]))
        if not r:
            st.info("This profile has no resume yet.")
            return
        st.markdown(f"**{r.get('file_name') or r.get('resume_id')}** "
                    f"(v{r.get('version') or '?'}) - parsed profile:")
        try:
            prof = api.get_resume_profile(u["id"], r["resume_id"]) or {}
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not load the parsed resume: {exc}")
            return
        if prof.get("summary"):
            st.write(prof["summary"])
        skills = prof.get("skills") or []
        if skills:
            st.markdown(f"**Skills ({len(skills)}):** " + ", ".join(map(str, skills[:40])))
        st.caption(f"{len(prof.get('experience') or [])} experience entr(ies) | "
                   f"{len(prof.get('education') or [])} education entr(ies)")
        with st.expander("Full parsed profile (JSON)"):
            st.json(prof)

    @st.dialog("Delete profile")
    def _delete(u: dict):
        st.warning(f"Delete profile **{u.get('name')}** (#{u['id']})?")
        st.caption(
            "Removes the profile and its resumes, config, memory, Resume Clinic "
            "reviews, and saved jobs. Workflow run history is preserved (kept for "
            "cost/analytics). This cannot be undone."
        )
        if st.checkbox("Yes, I understand - delete this profile", key=f"delconf_{u['id']}"):
            if st.button("Delete profile", type="primary"):
                try:
                    api.delete_user(u["id"])
                    # If the active profile was deleted, fall back to profile 0.
                    if str(u["id"]) == active_uid:
                        st.session_state.current_user_id = "0"
                        api.set_user_id("0")
                        st.session_state.config_cache = None
                    st.cache_data.clear()
                    st.success(f"Deleted profile #{u['id']}.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not delete profile: {exc}")

    # ── Add New ───────────────────────────────────────────────────────────────
    if st.button("➕ Add New", type="primary"):
        _add_new()

    if not users:
        st.info("No profiles yet. Use **Add New** to create one.")
        return

    # ── Profiles table (manual rows so each gets an actions column) ───────────
    st.markdown("")
    cols = st.columns([3, 3, 2, 3, 3])
    for c, label in zip(cols, ("Profile", "Note", "Created", "Resume", "Actions")):
        c.markdown(f"**{label}**")
    st.divider()

    for u in users:
        uid = str(u["id"])
        r = _active_resume(uid)
        c1, c2, c3, c4, c5 = st.columns([3, 3, 2, 3, 3])
        _active = " · active" if uid == active_uid else ""
        c1.markdown(f"**{u.get('name')}**  \n`#{u['id']}`{_active}")
        c2.write(u.get("note") or "—")
        c3.write(_fmt_ts(u.get("created_at")))
        if r:
            c4.write(f"{r.get('file_name') or 'resume'}  \nv{r.get('version') or '?'}")
        else:
            c4.write("_none_")

        with c5:
            a1, a2, a3, a4 = st.columns(4)
            if a1.button("✏️", key=f"edit_{uid}", help="Edit name / note"):
                _edit(u)
            if a2.button("📄", key=f"res_{uid}", help="Upload / overwrite resume"):
                _upload(u)
            if a3.button("👁️", key=f"view_{uid}", help="View resume",
                         disabled=r is None):
                _view(u)
            # Profile 0 (pre-existing data) is not deletable.
            if a4.button("🗑️", key=f"del_{uid}", help="Delete profile",
                         disabled=(uid == "0")):
                _delete(u)
        st.divider()
