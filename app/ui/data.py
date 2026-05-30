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
