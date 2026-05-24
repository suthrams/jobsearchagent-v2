"""Thin httpx wrapper for write actions against the FastAPI backend.

All control-path actions (start workflow, submit HITL decisions, fetch report)
go through this module. Read-only browse views go through db_reader.py instead.
"""
from __future__ import annotations

import os

import httpx

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
_TIMEOUT_GET = 5.0
_TIMEOUT_POST = 10.0


def start_workflow(
    resume_id: str,
    search_criteria: dict,
    workflow_type: str = "full_career_review",
    effective_config: dict | None = None,
    custom_urls: list[str] | None = None,
) -> dict:
    r = httpx.post(
        f"{BASE_URL}/workflows",
        json={
            "resume_id": resume_id,
            "search_criteria": search_criteria,
            "workflow_type": workflow_type,
            "effective_config": effective_config or {},
            "custom_urls": custom_urls or [],
        },
        timeout=_TIMEOUT_POST,
    )
    r.raise_for_status()
    return r.json()


def get_config() -> dict:
    r = httpx.get(f"{BASE_URL}/config", timeout=_TIMEOUT_GET)
    r.raise_for_status()
    return r.json()


def put_config(key: str, value: object) -> dict:
    r = httpx.put(
        f"{BASE_URL}/config",
        json={"key": key, "value": value},
        timeout=_TIMEOUT_POST,
    )
    r.raise_for_status()
    return r.json()


def reload_config() -> dict:
    """Rebuild the backend's WorkflowDependencies + graph from the current
    user_config, then return the now-effective per-agent assignment.

    Use this after a put_config() that changes runtime-overridable settings
    (per-agent provider/model picks). Replaces the previous "restart uvicorn
    to apply" workflow. Reload itself takes ~50-100ms (provider client init).
    Prompt/code changes still need a real process restart.
    """
    r = httpx.post(
        f"{BASE_URL}/config/reload",
        timeout=15.0,  # generous for cold-start client init
    )
    r.raise_for_status()
    return r.json()


def get_providers() -> dict:
    """Return registered providers + models + current per-agent assignment (ADR-053)."""
    r = httpx.get(f"{BASE_URL}/config/providers", timeout=_TIMEOUT_GET)
    r.raise_for_status()
    return r.json()


def get_workflow_status(workflow_id: str) -> dict:
    r = httpx.get(f"{BASE_URL}/workflows/{workflow_id}", timeout=_TIMEOUT_GET)
    r.raise_for_status()
    return r.json()


def retry_workflow(workflow_id: str) -> dict:
    """Re-submit a workflow interrupted by a server restart to the thread pool."""
    r = httpx.post(f"{BASE_URL}/workflows/{workflow_id}/retry", timeout=_TIMEOUT_POST)
    r.raise_for_status()
    return r.json()


def get_report(workflow_id: str) -> dict:
    r = httpx.get(f"{BASE_URL}/workflows/{workflow_id}/report", timeout=_TIMEOUT_GET)
    r.raise_for_status()
    return r.json()


# ── On-demand resume tailoring ───────────────────────────────────────────────

# Sized for the v5 tailoring prompts (ADR-056). Observed median latency for
# Sonnet tailoring + Haiku fidelity is ~60-70s end-to-end with the larger
# prompt + structured output (section_label + impact_rationale per bullet).
# 180s gives headroom for one provider-level retry without false timeouts.
# If the client DOES time out, the server typically still completes and
# persists the draft — see _TimeoutMaybePersisted handling in the UI.
_TIMEOUT_TAILOR = 180.0


def trigger_tailoring(workflow_id: str, job_id: str) -> dict:
    """Run tailoring + fidelity for one (workflow, job). Synchronous; returns the draft.

    POSTs to the workflow-scoped tailorings collection — creates a new tailoring resource.

    Note: if this raises httpx.ReadTimeout, the server-side work usually
    completes and persists anyway (the synchronous path can outlast the
    socket timeout). The Streamlit caller catches ReadTimeout specifically
    and tells the user to refresh — the new draft will appear in the list.
    """
    r = httpx.post(
        f"{BASE_URL}/workflows/{workflow_id}/jobs/{job_id}/tailorings",
        timeout=_TIMEOUT_TAILOR,
    )
    r.raise_for_status()
    return r.json()


def list_tailorings(workflow_id: str) -> dict:
    r = httpx.get(f"{BASE_URL}/workflows/{workflow_id}/tailorings", timeout=_TIMEOUT_GET)
    r.raise_for_status()
    return r.json()


def get_tailoring(tailoring_id: str) -> dict:
    r = httpx.get(f"{BASE_URL}/tailorings/{tailoring_id}", timeout=_TIMEOUT_GET)
    r.raise_for_status()
    return r.json()


def submit_tailoring_decision(tailoring_id: str, approval: str) -> dict:
    """approval ∈ {approve, revise, reject}.

    POSTs to the decisions collection on the tailoring — appends a new decision.
    """
    r = httpx.post(
        f"{BASE_URL}/tailorings/{tailoring_id}/decisions",
        json={"approval": approval},
        timeout=_TIMEOUT_POST,
    )
    r.raise_for_status()
    return r.json()


# ── ADR-057: per-job exclusion ───────────────────────────────────────────────

def exclude_job(job_id: str, reason: str | None = None) -> dict:
    r = httpx.post(
        f"{BASE_URL}/jobs/{job_id}/exclude",
        json={"reason": reason},
        timeout=_TIMEOUT_POST,
    )
    r.raise_for_status()
    return r.json()


def unexclude_job(job_id: str) -> dict:
    r = httpx.delete(
        f"{BASE_URL}/jobs/{job_id}/exclude",
        timeout=_TIMEOUT_POST,
    )
    r.raise_for_status()
    return r.json()
