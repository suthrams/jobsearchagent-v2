"""v2 Streamlit UI.

Layout (sidebar order is intentional — top of the page first):

  * Workflow History (default landing) — list of all runs, click to drill in
  * Workflow Detail                    — per-run unified view: jobs, scores,
                                         deep review, advice, prep, settings
                                         used for that run, constraints hit
  * Start New Run                      — settings inline + custom URLs textarea
  * Live Run Monitor                   — activity feed for the currently running run
  * Run Report                         — generated markdown report
  * Settings                           — view + edit user-overridable config
  * (Cross-run analytics — Top Matches, IC/Architect/Mgmt Track, Companies)

Browse views read data/v2.db directly via db_reader.py.
Control actions (start workflow, edit config) call FastAPI via api_client.py.
"""
from __future__ import annotations

import json

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

import yaml
import app.ui.api_client as api
from app.services.constraint_analyzer import analyze, summary_metrics
from app.services.cost_breakdown import compute_breakdown
from app.workflows.limits import MAX_LLM_CALLS_PER_RUN
from app.ui.db_reader import (
    load_agent_events,
    load_deep_review_results,
    load_interview_prep,
    load_job_pipeline,
    load_llm_calls,
    load_persisted_workflow_runs,
    load_recent_workflows,
    load_scored_jobs,
    load_step_executions,
    load_workflow_jobs,
    load_workflow_run,
    load_workflow_runs,
)


@st.cache_data
def _load_yaml_config() -> dict:
    try:
        with open("config/config.yaml", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


# ── Cached HTTP wrappers ──────────────────────────────────────────────────────
# Streamlit reruns the whole script on every interaction. Without caching, these
# endpoints would fire on every keystroke / sidebar click. TTL keeps them fresh
# enough to feel live; .clear() is called after any write that would invalidate.

@st.cache_data(ttl=10)
def _cached_list_tailorings(workflow_id: str) -> list[dict]:
    return api.list_tailorings(workflow_id).get("tailorings") or []


@st.cache_data(ttl=60)
def _cached_get_providers() -> dict | None:
    try:
        return api.get_providers()
    except Exception:
        return None


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Job Search Agent v2",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session state ─────────────────────────────────────────────────────────────

for _key, _default in (
    ("workflow_id", None),
    ("last_status", None),
    ("last_response", None),
    ("detail_workflow_id", None),
    ("detail_job_id", None),
    ("config_cache", None),
    ("sidebar_view", "Workflow History"),
):
    if _key not in st.session_state:
        st.session_state[_key] = _default


def _navigate(view_name: str, **state_updates) -> None:
    """Programmatic sidebar navigation.

    Cannot write directly to sidebar_view after the radio widget is instantiated.
    Store the destination in _pending_nav instead; the pre-sidebar block picks it
    up on the next render cycle before the radio widget is created.
    """
    for k, v in state_updates.items():
        st.session_state[k] = v
    st.session_state._pending_nav = view_name
    st.rerun()


def _fmt_ts(raw) -> str:
    """Format an ISO 8601 string as 'YYYY-MM-DD HH:MM:SS' for display."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return "—"
    s = str(raw).replace("T", " ")
    return s[:19] if len(s) >= 19 else s


# Auto-reconnect to the most recent workflow on first load
if "workflow_reconnect_attempted" not in st.session_state:
    st.session_state.workflow_reconnect_attempted = True
    if st.session_state.workflow_id is None:
        try:
            _recent = load_recent_workflows()
            if not _recent.empty and "workflow_id" in _recent.columns:
                _reconnect_id = _recent.iloc[0]["workflow_id"]
                st.session_state.workflow_id = _reconnect_id
                try:
                    _reconnect_resp = api.get_workflow_status(_reconnect_id)
                    st.session_state.last_status = _reconnect_resp.get("status")
                    st.session_state.last_response = _reconnect_resp
                except Exception:
                    pass
        except Exception:
            pass


# ── Shared helpers ────────────────────────────────────────────────────────────

def score_badge(score: int | None) -> str:
    if score is None:
        return "—"
    if score >= 80:
        return f"🟢 {score}"
    if score >= 65:
        return f"🟡 {score}"
    if score >= 50:
        return f"🟠 {score}"
    return f"🔴 {score}"


def _checked(flag) -> str:
    return "✅" if pd.notna(flag) and flag else "—"


def _get_nested(d: dict, keys: list[str]):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _label_with_cost(model_id: str, entries: list[dict]) -> str:
    for e in entries:
        if e["id"] == model_id:
            return f"{model_id}  ·  ${e['input_per_m']:.2f}/M in · ${e['output_per_m']:.2f}/M out"
    return model_id


def _bullets(label: str, items, *, sub: bool = False) -> None:
    """Render a labelled bullet list. No-op when items is empty / not a list.

    sub=True uses italic caption-style label for nested groups.
    """
    if not items or not isinstance(items, list):
        return
    if sub:
        st.markdown(f"_{label}_")
    else:
        st.markdown(f"**{label}**")
    for item in items:
        if isinstance(item, dict):
            # Best-effort flatten — render dicts as "key: value" bullets
            inner = "  ·  ".join(f"_{k}_: {v}" for k, v in item.items())
            st.markdown(f"- {inner}")
        else:
            st.markdown(f"- {item}")
    st.markdown("")  # blank line for breathing room


def _para(label: str, value) -> None:
    """Render a labelled paragraph. No-op when value is empty."""
    if not value:
        return
    st.markdown(f"**{label}**")
    st.write(value)
    st.markdown("")


# Friendly display labels for workflow stage names and progress strings.
# Keep this in sync with the actual current_step values written by workflow nodes.
_STAGE_LABEL = {
    "initialized":             "Starting up",
    "registered":              "Starting up",
    "job_discovery":           "Finding jobs",
    "load_resume":             "Loading resume",
    "scoring":                 "Scoring jobs",
    "score_jobs":              "Scoring jobs",
    "deep_review_in_progress": "Deep review",
    "review_completed":        "Computing advice",
    "no_qualifying_jobs":      "No matches above threshold",
    "career_advice":           "Generating career advice",
    "interview_prep":          "Generating interview prep",
    "tailoring":               "Tailoring resume",
    "completed":               "Done",
    "completed_with_errors":   "Done (with errors)",
    "failed":                  "Failed",
}


def _friendly_stage(current_step: str | None) -> str:
    if not current_step:
        return "—"
    return _STAGE_LABEL.get(current_step, str(current_step).replace("_", " ").title())


def _stage_progress(row: dict) -> str:
    """Build a 'where exactly is this run' string from a workflow_runs row.

    Examples:
      "5 / 10 scored"            during scoring
      "review 1 / 2 selected"    during deep review
      "8 jobs · 3 reviewed"      after completion
      ""                         when nothing meaningful to show
    """
    step = row.get("current_step") or ""
    status = row.get("status") or ""
    scored = int(row.get("jobs_scored") or 0)
    max_jobs = int(row.get("max_jobs") or 0) or None
    selected = int(row.get("selected_count") or 0)
    rounds = int(row.get("review_rounds_count") or 0)
    normalized = int(row.get("normalized_count") or 0)

    if status in ("completed", "completed_with_errors"):
        bits = []
        if scored:
            bits.append(f"{scored} scored")
        if selected:
            bits.append(f"{selected} reviewed")
        return " · ".join(bits) or "—"
    if status == "failed":
        return "halted"

    # Running: derive progress from the current step
    if step in ("job_discovery", "registered", "initialized"):
        return f"{normalized} found" if normalized else "discovering…"
    if step in ("load_resume",):
        return "parsing resume…"
    if step in ("scoring", "score_jobs"):
        if max_jobs:
            return f"{scored} / {max_jobs} scored"
        if normalized:
            return f"{scored} / {normalized} scored"
        return f"{scored} scored"
    if step == "deep_review_in_progress":
        if selected:
            return f"review {min(rounds, selected)} / {selected} jobs"
        return f"{rounds} review rounds"
    if step in ("career_advice", "interview_prep", "tailoring", "review_completed"):
        return f"{selected} job(s) advanced"
    return ""


def _get_config_cached() -> dict:
    """Pull config once per render and stash on session_state to avoid extra HTTP calls."""
    if st.session_state.config_cache is None:
        try:
            st.session_state.config_cache = api.get_config()
        except Exception as exc:
            st.session_state.config_cache = {"effective_config": _load_yaml_config(),
                                             "protected_keys": [],
                                             "_offline_reason": str(exc)}
    return st.session_state.config_cache


_CLAIM_BADGE = {"reword": "🟦 reword", "emphasize": "🟩 emphasize", "gap": "🟧 gap"}
_FIDELITY_RISK_BADGE = {"low": "🟢 low risk", "medium": "🟡 medium risk", "high": "🔴 high risk"}
_FIDELITY_STATUS_BADGE = {"pass": "🟢 fidelity pass", "needs_revision": "🟡 needs revision",
                          "fail": "🔴 fidelity fail"}
_DECISION_BADGE = {"approve": "🟢 approved", "revise": "🟡 needs revision", "reject": "🔴 rejected"}


def _render_tailored_bullets(label: str, bullets: list) -> None:
    if not bullets:
        return
    st.markdown(f"**{label}**")
    for i, b in enumerate(bullets):
        if not isinstance(b, dict):
            continue
        claim = b.get("claim_type") or "reword"
        risk = b.get("fidelity_risk") or "low"
        st.markdown(
            f"_Suggestion {i + 1}_  ·  "
            f"{_CLAIM_BADGE.get(claim, claim)}  ·  "
            f"{_FIDELITY_RISK_BADGE.get(risk, risk)}"
        )
        c1, c2 = st.columns(2)
        c1.markdown("_Original_")
        c1.markdown(f"> {b.get('original_text') or '_(none — net-new line)_'}")
        c2.markdown("_Suggested_")
        c2.markdown(f"> {b.get('suggested_text') or '—'}")
        ev = b.get("supporting_evidence") or ""
        if ev:
            st.caption(f"📎 Evidence from your resume: _{ev}_")
        unsupported = b.get("unsupported_claims") or []
        if unsupported:
            for u in unsupported:
                st.warning(f"Unsupported claim: {u}")
        st.markdown("")


def _render_tailoring_card(t: dict, on_decision) -> None:
    """Render one tailoring draft. on_decision(tailoring_id, choice) is invoked when
    one of the decision buttons is clicked."""
    tid = t.get("tailoring_id") or t.get("id") or ""
    draft = t.get("tailored") or {}
    fidelity = t.get("fidelity_review") or {}
    decision = t.get("decision")

    # Header row: status badges
    if decision:
        st.markdown(f"### {_DECISION_BADGE.get(decision, decision)}  ·  `{tid[:8]}…`")
    else:
        f_status = (fidelity or {}).get("overall_fidelity_status", "unknown")
        rec = (fidelity or {}).get("approval_recommendation")
        head = _FIDELITY_STATUS_BADGE.get(f_status, f"fidelity: {f_status}")
        if rec:
            head += f"  ·  recommended: **{rec}**"
        st.markdown(f"### {head}  ·  `{tid[:8]}…`")
    st.caption(f"Created `{_fmt_ts(t.get('created_at'))}`"
               + (f"  ·  Decided `{_fmt_ts(t.get('decided_at'))}`" if t.get("decided_at") else ""))

    # Fidelity flags
    flag_lines = []
    for fk, flabel in (
        ("unsupported_claims", "Unsupported claims"),
        ("fabricated_metrics", "Fabricated metrics"),
        ("inflated_scope_flags", "Inflated scope"),
        ("unsupported_technology_flags", "Unsupported tech"),
        ("unsupported_certification_flags", "Unsupported certifications"),
        ("required_removals", "Must remove"),
        ("required_revisions", "Must revise"),
    ):
        items = (fidelity or {}).get(fk) or []
        if items:
            flag_lines.append((flabel, items))
    if flag_lines:
        with st.expander("Fidelity flags", expanded=False):
            for label, items in flag_lines:
                st.markdown(f"**{label}**")
                for x in items:
                    st.markdown(f"- {x}")

    # Per-section diffs
    _render_tailored_bullets("Summary suggestions", draft.get("summary_suggestions") or [])
    _render_tailored_bullets("Experience bullet suggestions",
                             draft.get("experience_bullet_suggestions") or [])
    skills = draft.get("skills_section_suggestions") or []
    if skills:
        st.markdown("**Skills suggestions** _(additions to your existing skills section)_")
        for s in skills:
            st.markdown(f"- {s}")
    if draft.get("overall_tailoring_notes"):
        st.markdown(f"**Notes:** {draft['overall_tailoring_notes']}")
    if draft.get("fidelity_risk_summary"):
        st.caption(f"Risk summary: {draft['fidelity_risk_summary']}")

    # Decision buttons
    if not decision and tid:
        st.markdown("---")
        b1, b2, b3 = st.columns(3)
        if b1.button("✅ Approve", key=f"tail_app_{tid}"):
            on_decision(tid, "approve")
        if b2.button("✏️ Request revision", key=f"tail_rev_{tid}"):
            on_decision(tid, "revise")
        if b3.button("🚫 Reject", key=f"tail_rej_{tid}"):
            on_decision(tid, "reject")


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


# ── Flush pending navigation before the radio widget is created ───────────────
# _navigate() cannot write sidebar_view after the widget is instantiated, so it
# stores the destination in _pending_nav and we apply it here on the next cycle.
if st.session_state.get("_pending_nav"):
    st.session_state.sidebar_view = st.session_state.pop("_pending_nav")

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("Job Search Agent v2")
    st.markdown("---")
    view = st.radio(
        "View",
        [
            "Workflow History",
            "Workflow Detail",
            "Job Detail",
            "Start New Run",
            "Live Run Monitor",
            "Run Report",
            "Settings",
            "─── Cross-Run Analytics ───",
            "Top Matches",
            "IC Track",
            "Architect Track",
            "Management Track",
            "Companies",
        ],
        key="sidebar_view",
    )
    st.markdown("---")
    min_score = st.slider(
        "Minimum match score",
        min_value=0, max_value=100, value=75, step=5,
        help="Jobs with any track score (technical / architecture / leadership) at or above "
             "this value qualify for deep review and interview prep.",
    )
    st.markdown("---")
    search = st.text_input("Search title / company", placeholder="e.g. Staff Engineer")
    st.markdown("---")
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.session_state.config_cache = None
        st.rerun()
    if st.session_state.workflow_id:
        _wst = st.session_state.last_status or "unknown"
        _wicon = {
            "running": "🔵", "waiting_for_user": "🟡",
            "completed": "🟢", "failed": "🔴",
        }.get(_wst, "⚪")
        st.markdown("---")
        st.markdown(f"**Active Run** {_wicon} `{_wst}`")
        st.caption(f"`{st.session_state.workflow_id[:12]}…`")
        _wresp = st.session_state.last_response or {}
        _wstep = _wresp.get("current_step")
        if _wstep:
            st.caption(f"Step: `{_wstep}`")
        _wm = _wresp.get("run_metrics") or {}
        if _wm.get("llm_calls"):
            st.caption(f"{_wm['llm_calls']} calls · ${_wm.get('estimated_cost_usd', 0):.4f}")
        _b1, _b2 = st.columns(2)
        if _b1.button("Detail", key="sb_open_detail", use_container_width=True):
            _navigate("Workflow Detail", detail_workflow_id=st.session_state.workflow_id)
        if _b2.button("Live", key="sb_open_live", use_container_width=True):
            _navigate("Live Run Monitor")

if view.startswith("───"):
    st.info("Select a view from the sidebar.")
    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOW HISTORY (default landing)
# ══════════════════════════════════════════════════════════════════════════════

if view == "Workflow History":
    st.header("Workflow History")
    st.caption("All workflow runs, newest first. **Click any row** to open its Workflow Detail.")

    df = load_persisted_workflow_runs()

    # Fall back to the derived view (job_scores aggregation) if workflow_runs is still empty
    # — this keeps old runs visible while new ones populate the table.
    using_legacy = False
    if df.empty:
        df_legacy = load_workflow_runs()
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
        roles = _summarize_list(row.get("roles_json"))
        locs = _summarize_list(row.get("locations_json"))
        run_label = roles or "(no criteria snapshot)"
        if locs:
            run_label += f"  ·  📍 {locs}"

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
            "ID":       row.get("workflow_id", ""),
        })
    display_df = pd.DataFrame(rows_for_display)

    event = st.dataframe(
        display_df,
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Status":   st.column_config.TextColumn("Status",   width="small"),
            "Run":      st.column_config.TextColumn("Run",      width="large"),
            "Stage":    st.column_config.TextColumn("Stage",    width="medium"),
            "Progress": st.column_config.TextColumn("Progress", width="medium"),
            "Started":  st.column_config.TextColumn("Started",  width="small"),
            "Updated":  st.column_config.TextColumn("Updated",  width="small"),
            "Best":     st.column_config.NumberColumn("Best",   format="%d", width="small"),
            "≥":        st.column_config.NumberColumn("≥",      format="%d", width="small",
                                                       help="min_match_score for this run"),
            "URLs":     st.column_config.NumberColumn("URLs",   format="%d", width="small",
                                                       help="custom URLs supplied at run start"),
            "Cost":     st.column_config.NumberColumn("Cost",   format="$%.4f", width="small"),
            "ID":       st.column_config.TextColumn("ID",       width="small"),
        },
    )

    # Row click → drill into Workflow Detail
    sel = (event.selection.rows if event and getattr(event, "selection", None) else []) or []
    if sel:
        chosen = display_df.iloc[sel[0]]["ID"]
        if chosen and chosen != st.session_state.get("detail_workflow_id"):
            _navigate("Workflow Detail", detail_workflow_id=chosen, detail_job_id=None)

    # Surface the most recent error inline so failures are obvious without drilling in
    err_rows = df[df["error_message"].notna()] if "error_message" in df.columns else pd.DataFrame()
    if not err_rows.empty:
        with st.expander(f"⚠ Errors on {len(err_rows)} run(s)"):
            for _, e in err_rows.head(5).iterrows():
                st.markdown(f"- `{e['workflow_id'][:18]}…` — {str(e['error_message'])[:200]}")


# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOW DETAIL — unified per-run drill-down
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Workflow Detail":
    st.header("Workflow Detail")

    wf_id = st.session_state.detail_workflow_id or st.session_state.workflow_id
    wf_id = st.text_input("Workflow ID", value=wf_id or "",
                          help="Pick a run from Workflow History or paste an ID.")
    if not wf_id:
        st.info("No workflow selected.")
        st.stop()
    st.session_state.detail_workflow_id = wf_id

    record = load_workflow_run(wf_id)
    state = (record or {}).get("state") or {}
    status = (record or {}).get("status", "unknown")

    # Status header
    icon = {"running": "🔵", "completed": "🟢", "failed": "🔴", "completed_with_errors": "🟠"}.get(status, "⚪")
    h1, h2 = st.columns([3, 1])
    h1.markdown(f"### {icon} `{status}`")
    h2.caption(f"Started: {(record or {}).get('started_at', '—')}")

    metrics = summary_metrics(state)
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Discovered", metrics["jobs_discovered"])
    g2.metric("Scored", metrics["jobs_scored"])
    g3.metric("Selected (auto)", metrics["jobs_selected"])
    g4.metric("LLM cost", f"${metrics['estimated_cost_usd']:.4f}")
    g5, g6, g7, g8 = st.columns(4)
    g5.metric("LLM calls", metrics["llm_calls"])
    g6.metric("Tokens in", f"{metrics['tokens_input']:,}")
    g7.metric("Tokens out", f"{metrics['tokens_output']:,}")
    g8.metric("Review rounds", metrics["review_rounds"])

    # ── Pipeline table (jobs × stages) ────────────────────────────────────────
    st.markdown("---")
    st.subheader("Pipeline — jobs and pipeline stages")

    jobs_df = load_workflow_jobs(wf_id)
    if jobs_df.empty:
        st.info("No scored jobs yet for this run.")
    else:
        view_df = jobs_df.copy()
        view_df["✅ Reviewed"] = view_df["reviewed_at"].apply(_checked)
        view_df["✅ Advised"] = view_df["advised_at"].apply(_checked)
        view_df["✅ Prep"] = view_df["prep_at"].apply(_checked)
        st.dataframe(
            view_df[[
                "title", "company", "location", "url",
                "overall_score", "technical_score", "architecture_score", "leadership_score",
                "✅ Reviewed", "✅ Advised", "✅ Prep",
                "found_at", "scored_at",
            ]].rename(columns={
                "title": "Title", "company": "Company", "location": "Location", "url": "URL",
                "overall_score": "Overall",
                "technical_score": "Tech",
                "architecture_score": "Arch",
                "leadership_score": "Lead",
                "found_at": "Found", "scored_at": "Scored",
            }),
            hide_index=True,
            use_container_width=True,
            column_config={
                "URL":     st.column_config.LinkColumn("URL", width="small"),
                "Overall": st.column_config.ProgressColumn("Overall", min_value=0, max_value=100, format="%d"),
                "Tech":    st.column_config.ProgressColumn("Tech",    min_value=0, max_value=100, format="%d"),
                "Arch":    st.column_config.ProgressColumn("Arch",    min_value=0, max_value=100, format="%d"),
                "Lead":    st.column_config.ProgressColumn("Lead",    min_value=0, max_value=100, format="%d"),
            },
        )

        # Drill into a specific job — picker + button
        st.markdown("**Drill into a job →**")
        _options = {
            f"{r['title']} @ {r['company']}  ·  overall {int(r['overall_score'])}": r["job_id"]
            for _, r in jobs_df.iterrows()
        }
        _label = st.selectbox(
            "Pick a job to see all of its outputs (scoring, every review round, advice, prep) with timestamps",
            options=list(_options.keys()),
            label_visibility="collapsed",
        )
        if st.button("Drill in ▶", key="drill_in_job"):
            _navigate("Job Detail",
                      detail_workflow_id=wf_id,
                      detail_job_id=_options[_label])

    # ── Deep review + career advice (per job) ─────────────────────────────────
    rev_df = load_deep_review_results(wf_id)
    if not rev_df.empty:
        st.markdown("---")
        st.subheader("Deep Review & Career Advice")
        # Map review/advice timestamps from jobs_df for header timestamps
        ts_by_job = {
            r["job_id"]: (r.get("reviewed_at"), r.get("advised_at"))
            for _, r in (jobs_df.iterrows() if not jobs_df.empty else [])
        } if not jobs_df.empty else {}
        for _, row in rev_df.iterrows():
            jid = row["job_id"]
            _rev_ts, _adv_ts = ts_by_job.get(jid, (None, None))
            ts_caption = []
            if _rev_ts:
                ts_caption.append(f"reviewed `{_fmt_ts(_rev_ts)}`")
            if _adv_ts:
                ts_caption.append(f"advised `{_fmt_ts(_adv_ts)}`")
            ts_str = "  ·  ".join(ts_caption)
            with st.expander(
                f"Job `{jid}` — {row.get('overall_fit_summary', '—')[:80]}"
                + (f"  ·  {ts_str}" if ts_str else "")
            ):
                c1, c2 = st.columns(2)
                c1.markdown("**Resume Gaps** *(can tailor)*")
                try:
                    for g in (json.loads(row.get("resume_only_gaps_json") or "[]") or []):
                        c1.markdown(f"- {g}")
                except Exception:
                    pass
                c2.markdown("**Career Gaps** *(must not fabricate)*")
                try:
                    for g in (json.loads(row.get("career_gaps_observed_json") or "[]") or []):
                        c2.markdown(f"- {g}")
                except Exception:
                    pass
                if row.get("positioning_summary"):
                    st.markdown(f"**Positioning:** {row['positioning_summary']}")
                if row.get("recommended_next_action"):
                    st.markdown(f"**Recommended:** {row['recommended_next_action']}")

    # ── Interview Prep ────────────────────────────────────────────────────────
    prep_df = load_interview_prep(wf_id)
    if not prep_df.empty:
        st.markdown("---")
        st.subheader("Interview Prep")
        prep_ts = {
            r["job_id"]: r.get("prep_at")
            for _, r in (jobs_df.iterrows() if not jobs_df.empty else [])
        } if not jobs_df.empty else {}
        for _, row in prep_df.iterrows():
            jid = row["job_id"]
            _pts = prep_ts.get(jid)
            with st.expander(
                f"Job `{jid}`" + (f"  ·  prep `{_fmt_ts(_pts)}`" if _pts else "")
            ):
                try:
                    topics = json.loads(row.get("likely_topics_json") or "[]")
                    if topics:
                        st.markdown("**Likely topics:** " + ", ".join(topics))
                except Exception:
                    pass
                try:
                    plan = json.loads(row.get("seven_day_plan_json") or "[]")
                    if plan:
                        st.markdown("**7-day plan:**")
                        for item in plan:
                            st.markdown(f"- {item}")
                except Exception:
                    pass

    # ── Resume Tailoring (on-demand per job) ──────────────────────────────────
    st.markdown("---")
    st.subheader("Resume Tailoring")
    st.caption(
        "On-demand: pick a deep-reviewed job and generate evidence-bound "
        "section suggestions. Every suggestion cites the original line in your resume; "
        "missing experience is labelled as a gap, never rewritten as if present."
    )

    selected_jobs_state = state.get("selected_jobs") or []
    if not selected_jobs_state:
        st.info("No deep-reviewed jobs in this run — tailoring needs the per-job critic + advice context. "
                "Lower the threshold or broaden the search to qualify more jobs.")
    else:
        try:
            tail_index = _cached_list_tailorings(wf_id)
        except Exception as exc:
            st.error(f"Could not load existing tailorings: {exc}")
            tail_index = []

        by_job: dict[str, list[dict]] = {}
        for t in tail_index:
            by_job.setdefault(t.get("job_id", ""), []).append(t)

        def _decide(tid: str, choice: str) -> None:
            try:
                api.submit_tailoring_decision(tid, choice)
                _cached_list_tailorings.clear()
                st.success(f"Decision saved: {choice}")
                st.rerun()
            except Exception as exc:
                st.error(f"Decision failed: {exc}")

        for sj in selected_jobs_state:
            jid = sj.get("job_id") or sj.get("id") or ""
            if not jid:
                continue
            jtitle = sj.get("title") or "(untitled)"
            jcompany = sj.get("company") or ""
            existing = by_job.get(jid, [])
            label = f"**{jtitle}** @ {jcompany}  ·  job `{jid[:8]}…`"
            if existing:
                label += f"  ·  {len(existing)} draft(s)"
            with st.expander(label, expanded=False):
                trig_col, _ = st.columns([1, 4])
                if trig_col.button("✨ Generate new draft", key=f"trig_tail_{jid}"):
                    with st.spinner("Tailoring + fidelity review…"):
                        try:
                            api.trigger_tailoring(wf_id, jid)
                            _cached_list_tailorings.clear()
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Tailoring failed: {exc}")
                if not existing:
                    st.caption("No drafts yet for this job. Click **Generate new draft** to create one.")
                else:
                    for t in existing:
                        st.markdown("---")
                        _render_tailoring_card(t, _decide)

    # ── Settings used for this run ────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Settings used for this run")
    cfg_used = state.get("effective_config") or {}
    sc = state.get("search_criteria") or {}
    cc = state.get("custom_urls") or []
    if cfg_used or sc or cc:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Search criteria**")
            st.json(sc, expanded=False)
            if cc:
                st.markdown(f"**Custom URLs** ({len(cc)})")
                for u in cc:
                    st.markdown(f"- {u}")
        with col_b:
            st.markdown("**Effective config**")
            st.json(cfg_used, expanded=False)
    else:
        st.caption("No settings snapshot stored for this run (likely a pre-snapshot legacy run).")

    # ── Cost Breakdown (per-agent / per-model) ────────────────────────────────
    breakdown = compute_breakdown(wf_id)
    if breakdown["rows"]:
        st.markdown("---")
        st.subheader("Cost Breakdown")
        cost_df = pd.DataFrame(breakdown["rows"])
        cost_df = cost_df.rename(columns={
            "agent_name": "Agent", "provider": "Provider", "model": "Model",
            "calls": "Calls", "tokens_input": "Tokens in",
            "tokens_output": "Tokens out", "cost_usd": "Cost ($)",
            "avg_latency_ms": "Avg latency (ms)",
        })
        st.dataframe(
            cost_df, hide_index=True, use_container_width=True,
            column_config={
                "Cost ($)": st.column_config.NumberColumn(format="$%.4f"),
                "Avg latency (ms)": st.column_config.NumberColumn(format="%d"),
            },
        )
        agg = breakdown["aggregate"]
        st.caption(
            f"**Aggregate:** {agg['calls']} calls · "
            f"{agg['tokens_input']:,} in · {agg['tokens_output']:,} out · "
            f"**${agg['cost_usd']:.4f}** · ~{int(agg['avg_latency_ms'])} ms avg"
        )

    # ── Constraints + limits hit ──────────────────────────────────────────────
    findings = analyze(state)
    st.markdown("---")
    st.subheader("Limits & Constraints")
    if not findings:
        st.success("No execution limits clipped this run.")
    else:
        for f in findings:
            (st.warning if f["severity"] == "warning" else st.info)(f["message"])

    # ── Errors collected during the run ───────────────────────────────────────
    errors = state.get("errors") or []
    if errors:
        st.markdown("---")
        st.subheader(f"Errors ({len(errors)})")
        for err in errors:
            st.json(err, expanded=False)


# ══════════════════════════════════════════════════════════════════════════════
# JOB DETAIL — single-job drilldown for one workflow run
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Job Detail":
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


# ══════════════════════════════════════════════════════════════════════════════
# START NEW RUN — settings inline + custom URLs
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Start New Run":
    st.header("Start New Run")

    cfg = _get_config_cached()
    eff = cfg.get("effective_config", {})
    search_cfg = eff.get("search", {}) or {}
    scoring_cfg = eff.get("scoring", {}) or {}

    _default_roles = ", ".join(search_cfg.get("titles", []))
    _default_locations = ", ".join(search_cfg.get("locations", []))

    with st.expander("📋 Settings in play for this run", expanded=True):
        st.caption(
            "Defaults below come from your saved settings. Edits here apply to this run only "
            "and are persisted as overrides so future runs reuse them."
        )

    with st.form("start_run"):
        c1, c2 = st.columns(2)
        with c1:
            resume_id = st.text_input(
                "Resume ID", value="resume.pdf",
                help="Enter 'resume.pdf' on first run. Subsequent runs use the cached parsed profile.",
            )
            roles = st.text_input(
                "Roles (comma-separated)",
                value=_default_roles or "Staff Engineer, Principal Engineer",
            )
            locations = st.text_input(
                "Locations (comma-separated)",
                value=_default_locations or "Remote",
            )
        with c2:
            run_threshold = st.slider(
                "Min match score for this run",
                min_value=0, max_value=100,
                value=int(scoring_cfg.get("min_match_score", min_score)),
                step=5,
                help="Any track score (tech/arch/lead) at or above this triggers deep review + prep.",
            )
            max_jobs = st.number_input(
                "Max jobs to surface",
                min_value=1, max_value=50,
                value=int(search_cfg.get("max_jobs", 10)),
                help="Hard cap on how many discovered jobs the workflow processes.",
            )
            persist_prefs = st.checkbox(
                "Save these settings as my defaults for future runs",
                value=False,
            )

        st.markdown("**Custom job URLs** (optional, one per line — LinkedIn, company career pages, etc.)")
        custom_urls_raw = st.text_area(
            "URLs",
            value="",
            height=120,
            label_visibility="collapsed",
            placeholder="https://www.linkedin.com/jobs/view/123\nhttps://acme.com/careers/staff-engineer",
        )

        submitted = st.form_submit_button("Start Workflow")

    if submitted:
        custom_urls = [u.strip() for u in custom_urls_raw.splitlines() if u.strip()]

        search_criteria = {
            "roles": [r.strip() for r in roles.split(",") if r.strip()],
            "locations": [l.strip() for l in locations.split(",") if l.strip()],
        }
        effective_config = {
            "scoring": {
                "career_track": "all",
                "min_match_score": int(run_threshold),
            },
            "search": {
                "max_jobs": int(max_jobs),
            },
        }

        if persist_prefs:
            try:
                api.put_config("scoring.min_match_score", int(run_threshold))
                api.put_config("search.max_jobs", int(max_jobs))
                api.put_config("search.titles", search_criteria["roles"])
                api.put_config("search.locations", search_criteria["locations"])
                st.session_state.config_cache = None  # invalidate
            except Exception as exc:
                st.warning(f"Settings save failed (run will still start): {exc}")

        try:
            resp = api.start_workflow(
                resume_id, search_criteria,
                effective_config=effective_config,
                custom_urls=custom_urls,
            )
            st.session_state.workflow_id = resp["workflow_id"]
            st.session_state.last_status = "running"
            st.session_state.last_response = resp
            st.session_state.detail_workflow_id = resp["workflow_id"]
            st.success(f"Workflow started: `{resp['workflow_id']}`")
            st.info("Switch to **Live Run Monitor** to watch progress, or **Workflow Detail** when it finishes.")
        except Exception as exc:
            st.error(f"Failed to start workflow: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# LIVE RUN MONITOR — activity feed only (no HITL — auto-select drives the workflow)
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Live Run Monitor":
    st.header("Live Run Monitor")

    wf_id = st.session_state.workflow_id
    if not wf_id:
        st.warning("No active workflow in this session.")
        recent = load_recent_workflows()
        if not recent.empty:
            st.markdown("**Reconnect to a recent workflow:**")
            for _, row in recent.iterrows():
                _wf = row["workflow_id"]
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"`{_wf}`  \n{int(row.get('jobs_scored', 0))} scored")
                if c2.button("Reconnect", key=f"rc_{_wf}"):
                    st.session_state.workflow_id = _wf
                    st.rerun()
        st.stop()

    st.caption(f"Workflow: `{wf_id}`")
    cols = st.columns([1, 1, 4])
    if cols[0].button("Refresh"):
        st.cache_data.clear()
        try:
            resp = api.get_workflow_status(wf_id)
            st.session_state.last_response = resp
            st.session_state.last_status = resp.get("status")
        except Exception as exc:
            st.error(f"Could not fetch status: {exc}")

    status = st.session_state.last_status or "unknown"
    resp = st.session_state.last_response or {}

    if status == "running" and cols[1].button("▶ Retry", help="Re-submit after server restart"):
        try:
            api.retry_workflow(wf_id)
            st.session_state.last_status = "running"
            st.success("Workflow re-submitted.")
            st.rerun()
        except Exception as exc:
            st.error(f"Retry failed: {exc}")

    icon = {"running": "🔵", "completed": "🟢", "failed": "🔴"}.get(status, "⚪")
    st.markdown(f"**Status:** {icon} `{status}`")
    if resp.get("current_step"):
        st.markdown(f"**Step:** `{resp['current_step']}`")

    metrics = resp.get("run_metrics") or {}
    if metrics:
        m1, m2, m3 = st.columns(3)
        m1.metric("LLM calls", f"{metrics.get('llm_calls', 0)} / {MAX_LLM_CALLS_PER_RUN}")
        m2.metric("Est. cost", f"${metrics.get('estimated_cost_usd', 0):.4f}")
        m3.metric("Errors", len(resp.get("errors") or []))

    errors = resp.get("errors") or []
    if errors:
        with st.expander(f"Errors ({len(errors)})"):
            for err in errors:
                st.json(err)

    st.markdown("---")
    st.subheader("Run Activity")
    _STEP_ICON = {"completed": "✅", "failed": "❌", "started": "🔄"}

    steps_df = load_step_executions(wf_id)
    if steps_df.empty:
        st.caption("No steps recorded yet — workflow may still be initialising.")
    else:
        for _, row in steps_df.iterrows():
            ic = _STEP_ICON.get(row["status"], "⚪")
            dur = (
                f"  `{int(row['duration_ms']):,} ms`"
                if pd.notna(row.get("duration_ms")) and row["duration_ms"]
                else ""
            )
            notes = f"  — {row['notes']}" if row.get("notes") else ""
            st.markdown(f"{ic} **{row['step']}**{dur}{notes}")

    events_df = load_agent_events(wf_id)
    if not events_df.empty:
        with st.expander(f"Agent Events ({len(events_df)})", expanded=(status == "running")):
            display = events_df.copy()
            display[""] = display["event_type"].map(_STEP_ICON).fillna("⚪")
            display["Time"] = display["created_at"].str[11:19]
            display["Summary"] = display.apply(
                lambda r: (r.get("output_summary") or r.get("input_summary") or "")[:120],
                axis=1,
            )
            display["Duration"] = display["duration_ms"].apply(
                lambda x: f"{int(x):,} ms" if pd.notna(x) and x else "—"
            )
            st.dataframe(
                display[["", "Time", "agent_name", "event_type", "Duration", "Summary"]]
                .rename(columns={"agent_name": "Agent", "event_type": "Event"})
                .iloc[::-1],
                hide_index=True, use_container_width=True,
            )

    llm_df = load_llm_calls(wf_id)
    if not llm_df.empty:
        total_cost = llm_df["estimated_cost"].sum()
        total_tokens = int(llm_df["tokens_input"].sum() + llm_df["tokens_output"].sum())
        with st.expander(f"LLM Calls ({len(llm_df)}) · {total_tokens:,} tokens · ${total_cost:.4f}"):
            st.dataframe(
                llm_df[["agent_name", "model", "tokens_input", "tokens_output",
                        "estimated_cost", "latency_ms"]]
                .rename(columns={
                    "agent_name": "Agent", "model": "Model",
                    "tokens_input": "In", "tokens_output": "Out",
                    "estimated_cost": "Cost ($)", "latency_ms": "Latency (ms)",
                })
                .iloc[::-1],
                hide_index=True, use_container_width=True,
            )

    if status == "completed":
        st.success("Workflow complete — open it in **Workflow Detail** for a unified view.")
    elif status == "failed":
        st.error("Workflow failed — see errors above.")


# ══════════════════════════════════════════════════════════════════════════════
# RUN REPORT
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Run Report":
    st.header("Run Report")
    wf_id = st.session_state.workflow_id
    if not wf_id:
        st.warning("No active workflow.")
        st.stop()
    status = st.session_state.last_status
    if status not in ("completed", "completed_with_errors"):
        st.info(f"Report is available when the workflow completes. Current status: `{status or 'not started'}`.")
        st.stop()
    try:
        resp = api.get_report(wf_id)
        report = resp.get("report") or {}
        st.caption(f"Generated: {report.get('generated_at', '—')}")
        st.markdown(report.get("markdown", "_No report content._"))
        st.download_button(
            "Download Markdown",
            data=report.get("markdown", ""),
            file_name=f"report_{wf_id}.md",
            mime="text/markdown",
        )
    except Exception as exc:
        st.error(f"Could not fetch report: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Settings":
    st.header("Settings")

    cfg = _get_config_cached()
    eff = cfg.get("effective_config", {}) or {}
    protected = set(cfg.get("protected_keys", []) or [])
    if cfg.get("_offline_reason"):
        st.warning(f"Backend not reachable — read-only fallback. ({cfg['_offline_reason']})")

    st.caption(
        "Edit the values below to update your defaults. Protected keys (LLM models, "
        "execution limits, retention windows) are read-only and live in `config/config.yaml`."
    )

    def _save(key: str, value: object) -> None:
        try:
            api.put_config(key, value)
            st.success(f"Saved `{key}`")
            st.session_state.config_cache = None
        except Exception as exc:
            st.error(f"Save failed for `{key}`: {exc}")

    # ── Search ─────────────────────────────────────────────────────────────
    st.subheader("Search")
    search = (eff.get("search") or {}).copy()

    titles_str = st.text_area(
        "search.titles (comma-separated)",
        value=", ".join(search.get("titles", [])),
        height=80,
    )
    if st.button("Save titles"):
        _save("search.titles",
              [t.strip() for t in titles_str.split(",") if t.strip()])

    locations_str = st.text_area(
        "search.locations (comma-separated)",
        value=", ".join(search.get("locations", [])),
        height=60,
    )
    if st.button("Save locations"):
        _save("search.locations",
              [l.strip() for l in locations_str.split(",") if l.strip()])

    max_jobs = st.number_input(
        "search.max_jobs", min_value=1, max_value=50,
        value=int(search.get("max_jobs", 10)),
    )
    if st.button("Save max_jobs"):
        _save("search.max_jobs", int(max_jobs))

    # ── Scoring ────────────────────────────────────────────────────────────
    st.subheader("Scoring")
    scoring = (eff.get("scoring") or {}).copy()
    threshold = st.slider(
        "scoring.min_match_score (any track ≥ this triggers deep review)",
        min_value=0, max_value=100,
        value=int(scoring.get("min_match_score", 75)),
        step=5,
    )
    if st.button("Save min_match_score"):
        _save("scoring.min_match_score", int(threshold))

    # ── Salary ─────────────────────────────────────────────────────────────
    st.subheader("Salary")
    salary = (eff.get("salary") or {}).copy()
    salary_min = st.number_input(
        "salary.min_desired (USD)",
        min_value=0, max_value=10_000_000,
        value=int(salary.get("min_desired", 0)),
        step=10_000,
    )
    if st.button("Save salary.min_desired"):
        _save("salary.min_desired", int(salary_min))

    # ── Staleness ──────────────────────────────────────────────────────────
    st.subheader("Staleness")
    staleness = (eff.get("staleness") or {}).copy()
    max_days = st.number_input(
        "staleness.max_days (skip postings older than this)",
        min_value=1, max_value=365,
        value=int(staleness.get("max_days", 14)),
    )
    if st.button("Save staleness.max_days"):
        _save("staleness.max_days", int(max_days))

    # ── Agent Models (per ADR-053) ─────────────────────────────────────────
    st.markdown("---")
    st.subheader("Agent Models")
    st.caption(
        "Pick a provider and model per agent. Indicative cost shown per million tokens. "
        "Saves take effect after the **backend restarts**. In-flight workflows keep "
        "their original assignment."
    )

    providers_payload = _cached_get_providers()
    if providers_payload is None:
        st.warning("Couldn't reach `/config/providers` (backend may be down or restarting).")

    if providers_payload:
        catalog = providers_payload.get("providers", {}) or {}
        agent_assignment = providers_payload.get("agent_assignment", {}) or {}

        if not catalog.get("openai", {}).get("available", False):
            st.info(
                "OpenAI provider is not registered (no `OPENAI_API_KEY` in `.env`). "
                "Add the key and restart the backend to enable OpenAI models."
            )

        # One row per agent; provider dropdown then a model dropdown filtered by it.
        for agent_name in sorted(agent_assignment.keys()):
            assignment = agent_assignment[agent_name]
            current_provider = assignment.get("provider", "claude")
            current_model = assignment.get("model", "")

            with st.expander(
                f"`{agent_name}`  ·  current: **{current_provider}** / `{current_model}`",
                expanded=False,
            ):
                # Provider options — only show those the server reports as available
                provider_options = [
                    p for p, info in catalog.items()
                    if info.get("available", False) or p == current_provider
                ]
                provider_choice = st.selectbox(
                    "Provider",
                    options=provider_options,
                    index=provider_options.index(current_provider) if current_provider in provider_options else 0,
                    key=f"prov_{agent_name}",
                )

                # Model options for the chosen provider
                model_entries = catalog.get(provider_choice, {}).get("models", []) or []
                model_ids = [m["id"] for m in model_entries]
                model_idx = model_ids.index(current_model) if current_model in model_ids else 0
                model_choice = st.selectbox(
                    "Model",
                    options=model_ids,
                    index=model_idx if model_ids else 0,
                    format_func=lambda mid: _label_with_cost(mid, model_entries),
                    key=f"model_{agent_name}",
                )

                if st.button("Save", key=f"save_{agent_name}"):
                    try:
                        api.put_config(f"agents.{agent_name}.provider", provider_choice)
                        api.put_config(f"agents.{agent_name}.model", model_choice)
                        st.session_state.config_cache = None
                        _cached_get_providers.clear()
                        st.success(
                            f"Saved {agent_name} → {provider_choice}/{model_choice}. "
                            "Restart the backend for it to take effect."
                        )
                    except Exception as exc:
                        st.error(f"Save failed: {exc}")

    # ── Read-only protected ────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Read-only (protected)")
    st.caption("These cannot be changed via the UI to prevent accidental cost spikes or instability.")
    st.json({k: _get_nested(eff, k.split(".")) for k in sorted(protected)
             if _get_nested(eff, k.split(".")) is not None}, expanded=False)


# ══════════════════════════════════════════════════════════════════════════════
# CROSS-RUN ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Top Matches":
    st.header("Top Matches (across all runs)")
    df = load_scored_jobs()
    if df.empty:
        st.warning("No scored jobs found.")
        st.stop()
    if search:
        mask = (
            df["title"].str.contains(search, case=False, na=False)
            | df["company"].str.contains(search, case=False, na=False)
        )
        df = df[mask]
    filtered = df[df["overall_score"] >= min_score].copy()
    m1, m2, m3 = st.columns(3)
    m1.metric("Total scored", len(df))
    m2.metric(f"Score >= {min_score}", len(filtered))
    m3.metric("Companies", filtered["company"].nunique())
    render_track_table(filtered, "overall_score", min_score)


elif view == "IC Track":
    st.header("IC Engineering Track")
    df = load_scored_jobs()
    if df.empty:
        st.warning("No scored jobs found.")
        st.stop()
    render_track_table(df, "technical_score", min_score)


elif view == "Architect Track":
    st.header("Architect Track")
    df = load_scored_jobs()
    if df.empty:
        st.warning("No scored jobs found.")
        st.stop()
    render_track_table(df, "architecture_score", min_score)


elif view == "Management Track":
    st.header("Management Track")
    df = load_scored_jobs()
    if df.empty:
        st.warning("No scored jobs found.")
        st.stop()
    render_track_table(df, "leadership_score", min_score)


elif view == "Companies":
    st.header("Top Target Companies")
    df = load_scored_jobs()
    if df.empty:
        st.warning("No scored jobs found.")
        st.stop()
    agg = (
        df.groupby("company")
        .agg(
            jobs=("job_id", "count"),
            best_overall=("overall_score", "max"),
            best_technical=("technical_score", "max"),
            best_arch=("architecture_score", "max"),
            best_lead=("leadership_score", "max"),
        )
        .reset_index()
        .sort_values("best_overall", ascending=False)
    )
    agg = agg[agg["best_overall"] >= min_score]
    top = agg.head(20).sort_values("best_overall")
    fig = px.bar(
        top, x="best_overall", y="company", orientation="h",
        color="best_overall", color_continuous_scale="teal",
        labels={"best_overall": "Best Score", "company": "Company"},
        title=f"Top {len(top)} Companies by Best Match Score",
        text="best_overall",
    )
    fig.update_layout(showlegend=False, coloraxis_showscale=False, height=500)
    fig.update_traces(textposition="outside")
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(
        agg.rename(columns={
            "company": "Company", "jobs": "Roles",
            "best_overall": "Best", "best_technical": "Tech",
            "best_arch": "Arch", "best_lead": "Lead",
        }),
        hide_index=True, use_container_width=True,
    )


