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


def submit_job_selection(workflow_id: str, selected_job_ids: list[str]) -> dict:
    r = httpx.post(
        f"{BASE_URL}/workflows/{workflow_id}/decisions",
        json={
            "decision_type": "select_jobs_for_deep_review",
            "selected_job_ids": selected_job_ids,
        },
        timeout=_TIMEOUT_POST,
    )
    r.raise_for_status()
    return r.json()


def submit_tailoring_approval(workflow_id: str, approval: str) -> dict:
    r = httpx.post(
        f"{BASE_URL}/workflows/{workflow_id}/decisions",
        json={"decision_type": "approve_tailoring", "approval": approval},
        timeout=_TIMEOUT_POST,
    )
    r.raise_for_status()
    return r.json()


def get_report(workflow_id: str) -> dict:
    r = httpx.get(f"{BASE_URL}/workflows/{workflow_id}/report", timeout=_TIMEOUT_GET)
    r.raise_for_status()
    return r.json()


# ── On-demand resume tailoring ───────────────────────────────────────────────

_TIMEOUT_TAILOR = 60.0  # tailoring + fidelity round-trip can take 10-20s with cold caches


def trigger_tailoring(workflow_id: str, job_id: str) -> dict:
    """Run tailoring + fidelity for one (workflow, job). Synchronous; returns the draft.

    POSTs to the workflow-scoped tailorings collection — creates a new tailoring resource.
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
