"""Workflow History view - the default landing; all runs newest-first.

Phase 4 of the UI refactor (docs/architecture/ui_refactor_plan.md). Extracted
verbatim into render(ctx); all st.* calls run inside render().
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from app.ui.db_reader import load_persisted_workflow_runs, load_workflow_runs
from app.ui.formatting import _fmt_ts, _friendly_stage, _stage_progress
from app.ui.nav import ViewContext, _navigate


def render(ctx: ViewContext) -> None:
    st.header("Workflow History")
    st.caption("All workflow runs, newest first. **Select a row, then click Open detail** "
               "(or just click a different row to switch).")

    _uid = st.session_state.current_user_id
    df = load_persisted_workflow_runs(user_id=_uid)

    # Fall back to the derived view (job_scores aggregation) if workflow_runs is still empty
    # — this keeps old runs visible while new ones populate the table.
    using_legacy = False
    if df.empty:
        df_legacy = load_workflow_runs(user_id=_uid)
        if df_legacy.empty:
            st.info("No workflow runs yet. Start one from **Start New Run**.")
            st.stop()
        df = df_legacy.rename(columns={"id": "workflow_id"})
        df["status"] = df.get("status", "completed")
        df["current_step"] = df.get("current_step", "—")
        df["completed_at"] = df.get("completed_at", df.get("updated_at"))
        df["error_message"] = df.get("error_message", None)
        # Fill in the columns the new query exposes so the table renders consistently
        for _col in ("max_jobs", "normalized_count", "selected_count",
                     "review_rounds_count", "cost_usd", "llm_calls",
                     "threshold", "custom_url_count", "roles_json", "locations_json"):
            if _col not in df.columns:
                df[_col] = None
        using_legacy = True

    # Filter input
    fcol1, fcol2 = st.columns([3, 1])
    q = fcol1.text_input(
        "Filter by role / location / workflow ID prefix",
        value="", placeholder="e.g. Staff Engineer  |  Atlanta  |  3fa85f",
    )
    only_with_jobs = fcol2.checkbox("Only runs with scored jobs", value=False)

    if q.strip():
        ql = q.strip().lower()
        def _match(row):
            blob = " ".join([
                str(row.get("workflow_id", "")),
                str(row.get("roles_json", "") or ""),
                str(row.get("locations_json", "") or ""),
            ]).lower()
            return ql in blob
        df = df[df.apply(_match, axis=1)]
    if only_with_jobs and "jobs_scored" in df.columns:
        df = df[df["jobs_scored"].fillna(0) > 0]

    df = df.reset_index(drop=True)

    # Top metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Runs shown", len(df))
    m2.metric("Total Jobs Scored", int(df["jobs_scored"].sum()) if "jobs_scored" in df.columns else 0)
    _completed = (df["status"] == "completed").sum() if "status" in df.columns else 0
    m3.metric("Completed", int(_completed))
    _failed = df["status"].isin(["failed", "completed_with_errors"]).sum() if "status" in df.columns else 0
    m4.metric("Failed / Errors", int(_failed))

    if using_legacy:
        st.caption("⚠ Showing legacy aggregation — these runs predate the workflow_runs snapshot. "
                   "Stage / progress / threshold won't be visible until you run a new workflow.")

    # ── Build a display dataframe with friendly columns ──────────────────────
    _STATUS_DISPLAY = {
        "running":               "🔵 Running",
        "waiting_for_user":      "🟡 Waiting",
        "completed":             "🟢 Done",
        "completed_with_errors": "🟠 Done (errors)",
        "failed":                "🔴 Failed",
    }

    def _summarize_list(raw, max_items=2) -> str:
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(items, list) or not items:
                return ""
            shown = ", ".join(items[:max_items])
            if len(items) > max_items:
                shown += f" +{len(items) - max_items}"
            return shown
        except Exception:
            return ""

    rows_for_display: list[dict] = []
    for _, row in df.iterrows():
        # Keep the table scannable: one role + a "+N" badge for the rest, same for locations.
        # Full criteria are visible on the Workflow Detail screen.
        roles = _summarize_list(row.get("roles_json"), max_items=1)
        locs = _summarize_list(row.get("locations_json"), max_items=1)
        run_label = roles or "(no criteria)"
        if locs:
            run_label += f"  ·  {locs}"

        full_id = row.get("workflow_id", "") or ""

        rows_for_display.append({
            "Status":   _STATUS_DISPLAY.get(row.get("status", ""), str(row.get("status", "—"))),
            "Run":      run_label,
            "Stage":    _friendly_stage(row.get("current_step")),
            "Progress": _stage_progress(row.to_dict()),
            "Started":  _fmt_ts(row.get("started_at")),
            "Updated":  _fmt_ts(row.get("completed_at") or row.get("updated_at")),
            "Best":     int(row["best_score"]) if pd.notna(row.get("best_score")) else None,
            "≥": int(row["threshold"]) if pd.notna(row.get("threshold")) else None,
            "URLs":     int(row["custom_url_count"]) if pd.notna(row.get("custom_url_count")) else 0,
            "Cost":     float(row["cost_usd"]) if pd.notna(row.get("cost_usd")) else 0.0,
            "ID":       (full_id[:8] + "…") if len(full_id) > 8 else full_id,
        })
    display_df = pd.DataFrame(rows_for_display)

    event = st.dataframe(
        display_df,
        key="wf_history_table",
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Status":   st.column_config.TextColumn("Status",   width="small"),
            "Run":      st.column_config.TextColumn("Run",      width="medium",
                                                       help="Roles and locations searched (full list on the detail screen)"),
            "Stage":    st.column_config.TextColumn("Stage",    width="small"),
            "Progress": st.column_config.TextColumn("Progress", width="small"),
            "Started":  st.column_config.TextColumn("Started",  width="small"),
            "Updated":  st.column_config.TextColumn("Updated",  width="small"),
            "Best":     st.column_config.NumberColumn("Best",   format="%d", width="small"),
            "≥":        st.column_config.NumberColumn("≥",      format="%d", width="small",
                                                       help="min_match_score for this run"),
            "URLs":     st.column_config.NumberColumn("URLs",   format="%d", width="small",
                                                       help="custom URLs supplied at run start"),
            "Cost":     st.column_config.NumberColumn("Cost",   format="$%.4f", width="small"),
            "ID":       st.column_config.TextColumn("ID",       width="small",
                                                       help="Select the row, then click Open detail."),
        },
    )

    # Resolve the selected workflow_id from the dataframe widget. Selection state
    # is read from the keyed widget (st.session_state["wf_history_table"]) which
    # survives reruns, with a fallback to the event return value for the same
    # render cycle.
    sel: list[int] = []
    widget_state = st.session_state.get("wf_history_table")
    if widget_state and getattr(widget_state, "selection", None):
        sel = list(widget_state.selection.rows or [])
    if not sel and event and getattr(event, "selection", None):
        sel = list(event.selection.rows or [])

    chosen = ""
    if sel and sel[0] < len(df):
        chosen = str(df.iloc[sel[0]].get("workflow_id", "") or "")

    # Explicit navigation affordance. Row-click selection alone is easy to miss
    # (no visible focus ring) and the auto-navigate variant occasionally does
    # not fire if the selection state lands on the same id between renders.
    nav_col1, nav_col2 = st.columns([1, 4])
    open_clicked = nav_col1.button(
        "Open detail →",
        type="primary",
        disabled=not chosen,
        use_container_width=True,
        help="Select a row above, then click here to open its Workflow Detail.",
    )
    if chosen:
        nav_col2.caption(f"Selected: `{chosen}`")
    else:
        nav_col2.caption("Select any row above to enable Open detail.")

    if open_clicked and chosen:
        _navigate("Workflow Detail", detail_workflow_id=chosen, detail_job_id=None)
    # Convenience: also auto-navigate on a fresh row click when the user picks a
    # different run than the one currently loaded in the Detail view.
    elif chosen and chosen != st.session_state.get("detail_workflow_id"):
        _navigate("Workflow Detail", detail_workflow_id=chosen, detail_job_id=None)

    # Surface the most recent error inline so failures are obvious without drilling in
    err_rows = df[df["error_message"].notna()] if "error_message" in df.columns else pd.DataFrame()
    if not err_rows.empty:
        with st.expander(f"⚠ Errors on {len(err_rows)} run(s)"):
            for _, e in err_rows.head(5).iterrows():
                st.markdown(f"- `{e['workflow_id'][:18]}…` — {str(e['error_message'])[:200]}")
