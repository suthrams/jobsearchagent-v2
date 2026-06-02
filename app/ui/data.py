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

import streamlit as st
import yaml

import app.ui.api_client as api


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


@st.cache_data(ttl=15)
def _cached_scored_jobs(user_id: str | None, include_excluded: bool = False) -> dict:
    """Scored-jobs analytics via the API (ADR-075 Phase 3). `user_id` is a cache
    key. Degrades to an empty page when the backend is unavailable."""
    try:
        return api.list_scored_jobs(include_excluded=include_excluded)
    except Exception:
        return {"items": [], "total": 0, "limit": 0, "offset": 0}


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
