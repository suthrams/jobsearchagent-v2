"""Cached data-access wrappers for the v2 Streamlit UI.

Phase 2 of the UI refactor (docs/architecture/ui_refactor_plan.md). Streamlit
reruns the whole script on every interaction, so these wrap the FastAPI control
client (api_client) and the local YAML config with `@st.cache_data` / a
session-state cache to avoid firing on every keystroke. `.clear()` is called from
the entrypoint after any write that would invalidate them.

These are the only refactor modules that legitimately import streamlit (they need
`st.cache_data` / `st.session_state`); they still do not render anything.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
import yaml

import app.ui.api_client as api


def _df(call, *args, **kwargs) -> pd.DataFrame:
    """Run an api_client list call and return its items as a DataFrame, or an empty
    DataFrame if the backend is unavailable (ADR-075). Drop-in for the old
    db_reader.load_* functions, which also returned DataFrames."""
    try:
        return pd.DataFrame(call(*args, **kwargs).get("items") or [])
    except Exception:
        return pd.DataFrame()


@st.cache_data
def _load_yaml_config() -> dict:
    try:
        with open("config/config.yaml", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


@st.cache_data(ttl=10)
def _cached_list_tailorings(workflow_id: str) -> list[dict]:
    return api.list_tailorings(workflow_id).get("tailorings") or []


@st.cache_data(ttl=60)
def _cached_get_providers() -> dict | None:
    try:
        return api.get_providers()
    except Exception:
        return None


@st.cache_data(ttl=30)
def _cached_list_users() -> list[dict]:
    """Profiles for the sidebar selector (ADR-062). Default user 0 first."""
    try:
        return api.list_users().get("users") or []
    except Exception:
        return []


@st.cache_data(ttl=10)
def _cached_workflow_runs(user_id: str | None, limit: int = 50) -> dict:
    """Workflow History page via the API (ADR-075 Phase 1). `user_id` is a cache
    key (api_client attaches the acting profile itself). Degrades to an empty page
    when the backend is unavailable, so browse views never crash on a cold API."""
    try:
        return api.list_workflow_runs(limit=limit)
    except Exception:
        return {"items": [], "total": 0, "limit": limit, "offset": 0}


@st.cache_data(ttl=10)
def _cached_user_resumes(user_id: str | None) -> dict:
    """A profile's resumes via the API (ADR-075 Phase 2). Degrades to an empty
    page when the backend is unavailable so the picker views never crash."""
    try:
        return api.list_user_resumes(user_id)
    except Exception:
        return {"items": [], "total": 0, "limit": 0, "offset": 0}


@st.cache_data(ttl=10)
def _cached_favorites(user_id: str | None) -> list[dict]:
    """A profile's favorite jobs via the API (ADR-090). Degrades to an empty list
    when the backend is unavailable so the star toggles / clinic picker never crash."""
    try:
        return api.list_favorites(user_id)
    except Exception:
        return []


@st.cache_data(ttl=15)
def _cached_scored_jobs(user_id: str | None, include_excluded: bool = False) -> dict:
    """Scored-jobs analytics via the API (ADR-075 Phase 3). `user_id` is a cache
    key. Degrades to an empty page when the backend is unavailable."""
    try:
        return api.list_scored_jobs(include_excluded=include_excluded)
    except Exception:
        return {"items": [], "total": 0, "limit": 0, "offset": 0}


# ── Run-scoped reads (ADR-075 Phases 4-6) — return DataFrames, drop-in for db_reader ──

@st.cache_data(ttl=10)
def _cached_recent_workflows() -> pd.DataFrame:
    return _df(api.list_recent_workflows)


@st.cache_data(ttl=10)
def _cached_workflow_jobs(workflow_id: str, include_excluded: bool = True) -> pd.DataFrame:
    return _df(api.list_workflow_jobs, workflow_id, include_excluded)


@st.cache_data(ttl=30)
def _cached_deep_review_results(workflow_id: str) -> pd.DataFrame:
    return _df(api.list_deep_review_results, workflow_id)


@st.cache_data(ttl=30)
def _cached_interview_prep(workflow_id: str) -> pd.DataFrame:
    return _df(api.list_interview_prep, workflow_id)


@st.cache_data(ttl=5)
def _cached_step_executions(workflow_id: str) -> pd.DataFrame:
    return _df(api.list_step_executions, workflow_id)


@st.cache_data(ttl=5)
def _cached_agent_events(workflow_id: str) -> pd.DataFrame:
    return _df(api.list_agent_events, workflow_id)


@st.cache_data(ttl=5)
def _cached_llm_calls(workflow_id: str) -> pd.DataFrame:
    return _df(api.list_llm_calls, workflow_id)


@st.cache_data(ttl=30)
def _cached_job_pipeline(workflow_id: str, job_id: str) -> dict:
    try:
        return api.get_job_pipeline(workflow_id, job_id)
    except Exception:
        return {"job": None, "score": None, "review_rounds": [],
                "final_review": None, "advice": None, "prep": None}


@st.cache_data(ttl=10)
def _cached_workflow_detail(workflow_id: str) -> dict | None:
    try:
        return api.get_workflow_detail(workflow_id)
    except Exception:
        return None


@st.cache_data(ttl=10)
def _cached_cost_breakdown(workflow_id: str) -> dict:
    try:
        return api.get_cost_breakdown(workflow_id)
    except Exception:
        return {"rows": [], "aggregate": {}}


@st.cache_data(ttl=10)
def _cached_run_metrics(workflow_id: str) -> dict:
    try:
        return api.get_run_metrics(workflow_id)
    except Exception:
        return {"calls": 0, "tokens_input": 0, "tokens_output": 0, "cost_usd": 0.0,
                "duration_ms": 0, "started_at": None, "completed_at": None, "computed": True}


@st.cache_data(ttl=15)
def _cached_system_dashboard(days, view_uid: str | None) -> dict:
    """Composite System Dashboard payload via the API (ADR-075 Phase 7). Degrades
    to empty sections when the backend is unavailable."""
    try:
        return api.get_system_dashboard(days=days, view_user_id=view_uid)
    except Exception:
        return {
            "cost": {"window_days": days, "totals": {"calls": 0, "tokens_input": 0,
                     "tokens_output": 0, "cost_usd": 0.0, "distinct_runs": 0},
                     "by_agent": [], "by_model": []},
            "daily_trend": [], "top_runs": [], "all_runs": [], "top_calls": [],
            "security": {"total": 0, "by_type": [], "by_severity": {"high": 0, "warning": 0, "info": 0}, "recent": []},
            "performance": {"llm": {"p50_ms": 0.0, "p95_ms": 0.0, "calls": 0},
                            "agent": {"p50_ms": 0.0, "p95_ms": 0.0, "events": 0},
                            "slowest_agents": [], "slowest_steps": []},
            "reliability": {"runs_total": 0, "runs_completed": 0, "runs_failed": 0,
                            "success_rate": 0.0, "agent_failures": 0,
                            "failures_by_agent": [], "recent_failures": []},
            "scalability": {"avg_jobs_per_run": 0.0, "runs_per_day": 0.0,
                            "peak_jobs_in_run": 0, "distinct_runs": 0},
            "api": {"total": 0, "error_count": 0, "error_rate": 0.0, "p50_ms": 0.0,
                    "p95_ms": 0.0, "by_endpoint": []},
            "decisions": {"total": 0, "by_type": {}, "by_value": {}, "recent": []},
            "profiles": [],
        }


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
