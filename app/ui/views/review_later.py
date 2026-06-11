"""Review-later view - the "Maybe / Review later" saved-job list (ADR-100).

A per-profile bucket the user moves relevance/clearance filter-dropped jobs into,
from the "Why filtered out" panel on a search's detail, to reconsider later. Phase 1
is curation only: list the saved jobs with their link + source, and let the user
remove them. (Scoring a saved job on demand is ADR-100 Phase 2.)

All st.* calls live in render(ctx); importing this module runs no Streamlit code.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import app.ui.api_client as api
from app.ui.data import _cached_review_later
from app.ui.formatting import source_label
from app.ui.nav import ViewContext


def render(ctx: ViewContext) -> None:
    st.header("Maybe / Review later")
    st.caption(
        "Jobs you pulled back out of a search's filtered-out bucket to consider "
        "later. Nothing here has been scored - it is your own holding list. Open a "
        "job to review it, or remove it when you have decided."
    )

    uid = str(st.session_state.get("current_user_id", "0"))
    rows = _cached_review_later(uid)
    if not rows:
        st.info(
            "Your Review later list is empty. On a search's detail, expand "
            "\"Why N job(s) were filtered out\" and use \"Move to Review later\" to "
            "set a dropped job aside here."
        )
        return

    table = [
        {
            "Title": r.get("title") or r.get("job_id") or "(unknown job)",
            "Company": (r.get("company") or "").strip() or "—",
            "Source": source_label(r.get("source")),
            "Link": (r.get("url") or "").strip(),
        }
        for r in rows
    ]
    st.dataframe(
        pd.DataFrame(table),
        key="review_later_table",
        hide_index=True,
        use_container_width=True,
        column_config={
            "Title": st.column_config.TextColumn("Title", width="medium"),
            "Company": st.column_config.TextColumn("Company", width="small"),
            "Source": st.column_config.TextColumn("Source", width="small"),
            "Link": st.column_config.LinkColumn("Link", width="small", display_text="open"),
        },
    )

    # ADR-100 Phase 2: send one job through research + scoring on demand. Once
    # scored it joins the regular route (Matches, deep-review, tailoring, prep).
    scorable = {
        (r.get("title") or r.get("job_id")): (r.get("workflow_id"), r.get("job_id"))
        for r in rows
        if r.get("job_id") and r.get("workflow_id")
    }
    if scorable:
        st.markdown("**Score a job** (research + score it, then it appears in Matches)")
        pick = st.selectbox(
            "Job to score", options=list(scorable.keys()), key="review_later_score_pick",
        )
        if st.button("Score this job", key="review_later_score_btn"):
            wf, jid = scorable[pick]
            try:
                with st.spinner("Researching + scoring this job..."):
                    res = api.score_job(wf, jid)
                _score = res.get("overall_score")
                if res.get("already_scored"):
                    st.info(f"Already scored (overall {_score}). See it in Matches.")
                else:
                    st.success(
                        f"Scored '{pick}' (overall {_score}). It is now in Matches and "
                        "ready for deep review / tailoring / interview prep."
                    )
            except Exception as exc:  # noqa: BLE001 - surface, never crash
                st.error(f"Could not score '{pick}': {exc}")

    st.markdown("---")
    # Remove jobs you have finished considering.
    label_to_id = {
        (r.get("title") or r.get("job_id")): r.get("job_id") for r in rows if r.get("job_id")
    }
    picked = st.multiselect(
        "Remove from Review later",
        options=list(label_to_id.keys()),
        key="review_later_remove_pick",
    )
    if st.button("Remove selected", disabled=not picked, key="review_later_remove_btn"):
        removed = 0
        for lbl in picked:
            try:
                api.remove_review_later(uid, label_to_id[lbl])
                removed += 1
            except Exception as exc:  # noqa: BLE001 - surface, never crash
                st.warning(f"Could not remove '{lbl}': {exc}")
        if removed:
            _cached_review_later.clear()
            st.success(f"Removed {removed} job(s).")
            st.rerun()
