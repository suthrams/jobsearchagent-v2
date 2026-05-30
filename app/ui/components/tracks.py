"""The cross-run track-table renderer.

Phase 2 of the UI refactor (docs/architecture/ui_refactor_plan.md). Renders the
scored-jobs table used by the Top Matches and per-track analytics views. ``st.*``
runs only inside the function body.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st


def render_track_table(df: pd.DataFrame, score_col: str, min_score: int) -> None:
    filtered = df[df[score_col] >= min_score].sort_values(score_col, ascending=False).copy()
    display = filtered[
        ["job_id", "title", "company", "location", score_col,
         "match_summary", "recommended_next_action", "url"]
    ].rename(columns={
        score_col: "Score",
        "match_summary": "Summary",
        "recommended_next_action": "Agent Recommendation",
        "url": "URL",
    }).reset_index(drop=True)

    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
        column_config={
            "job_id":      st.column_config.TextColumn("Job ID",     width="small"),
            "title":       st.column_config.TextColumn("Title",      width="large"),
            "company":     st.column_config.TextColumn("Company",    width="medium"),
            "location":    st.column_config.TextColumn("Location",   width="medium"),
            "Score":       st.column_config.ProgressColumn("Score",  min_value=0, max_value=100, format="%d"),
            "Summary":              st.column_config.TextColumn("Summary",             width="large"),
            "Agent Recommendation": st.column_config.TextColumn("Agent Recommendation", width="large"),
            "URL":         st.column_config.LinkColumn("Link",       width="small"),
        },
    )
    st.caption(f"{len(filtered)} jobs with score >= {min_score}")
