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

# ADR-062: the acting profile, set once per Streamlit rerun from
# st.session_state["current_user_id"]. Attached as the `user_id` query param on
# the calls whose backend endpoints resolve it via the identity seam. This is the
# client-side mirror of the single seam: one place sets it, callers don't repeat it.
_CURRENT_USER_ID: str | None = None


def set_user_id(user_id: str | None) -> None:
    global _CURRENT_USER_ID
    _CURRENT_USER_ID = str(user_id) if user_id is not None else None


def _user_params(extra: dict | None = None) -> dict:
    params = dict(extra or {})
    if _CURRENT_USER_ID is not None:
        params["user_id"] = _CURRENT_USER_ID
    return params


def start_workflow(
    resume_id: str,
    search_criteria: dict,
    workflow_type: str = "full_career_review",
    effective_config: dict | None = None,
    custom_urls: list[str] | None = None,
) -> dict:
    r = httpx.post(
        f"{BASE_URL}/workflows",
        params=_user_params(),
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


def submit_scoring_selection(workflow_id: str, selected_job_ids: list[str]) -> dict:
    """ADR-060 phase 2: tell the backend which discovered jobs to score.

    Valid only while the workflow is awaiting_scoring_selection (a manual-selection
    run parked after discovery). Returns 202 with the count being scored.
    """
    r = httpx.post(
        f"{BASE_URL}/workflows/{workflow_id}/scoring",
        json={"selected_job_ids": selected_job_ids},
        timeout=_TIMEOUT_POST,
    )
    r.raise_for_status()
    return r.json()


def get_config() -> dict:
    r = httpx.get(f"{BASE_URL}/config", params=_user_params(), timeout=_TIMEOUT_GET)
    r.raise_for_status()
    return r.json()


def put_config(key: str, value: object) -> dict:
    r = httpx.put(
        f"{BASE_URL}/config",
        params=_user_params(),
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
    r = httpx.get(f"{BASE_URL}/config/providers", params=_user_params(), timeout=_TIMEOUT_GET)
    r.raise_for_status()
    return r.json()


# ── ADR-062: profile management ──────────────────────────────────────────────

def list_users() -> dict:
    """All profiles (default user 0 first). Backs the sidebar profile selector."""
    r = httpx.get(f"{BASE_URL}/users", timeout=_TIMEOUT_GET)
    r.raise_for_status()
    return r.json()


def create_user(name: str, note: str | None = None) -> dict:
    """Create a profile; returns the new user with its assigned id."""
    r = httpx.post(
        f"{BASE_URL}/users",
        json={"name": name, "note": note},
        timeout=_TIMEOUT_POST,
    )
    r.raise_for_status()
    return r.json()


def upload_resume(user_id: int | str, file_bytes: bytes, filename: str) -> dict:
    """Upload + parse a PDF resume for a profile; returns the new resume id.

    Parsing may run the Claude enhancement pass, so this can take tens of
    seconds — use the generous tailoring-class timeout.
    """
    r = httpx.post(
        f"{BASE_URL}/users/{user_id}/resume",
        files={"file": (filename, file_bytes, "application/pdf")},
        timeout=_TIMEOUT_TAILOR,
    )
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


def trigger_tailoring(workflow_id: str, job_id: str,
                      auto_deep_review: bool = True) -> dict:
    """Run tailoring + fidelity for one (workflow, job). Synchronous; returns the draft.

    POSTs to the workflow-scoped tailorings collection — creates a new tailoring resource.

    ADR-061: when the job has no deep-review yet and auto_deep_review is true
    (default), the server runs the critic+auditor loop for it first.

    Note: if this raises httpx.ReadTimeout, the server-side work usually
    completes and persists anyway (the synchronous path can outlast the
    socket timeout). The Streamlit caller catches ReadTimeout specifically
    and tells the user to refresh — the new draft will appear in the list.
    """
    r = httpx.post(
        f"{BASE_URL}/workflows/{workflow_id}/jobs/{job_id}/tailorings",
        params={"auto_deep_review": str(auto_deep_review).lower()},
        timeout=_TIMEOUT_TAILOR,
    )
    r.raise_for_status()
    return r.json()


def trigger_deep_review(workflow_id: str, job_id: str) -> dict:
    """ADR-061: run the critic+auditor reflection loop for one scored job on demand.

    Synchronous; can take ~20-40s (up to MAX_REVIEW_ROUNDS rounds). Persists the
    review so a later tailoring call reuses it instead of re-reviewing.
    """
    r = httpx.post(
        f"{BASE_URL}/workflows/{workflow_id}/jobs/{job_id}/deep-review",
        timeout=_TIMEOUT_TAILOR,
    )
    r.raise_for_status()
    return r.json()


def trigger_interview_prep(workflow_id: str, job_id: str) -> dict:
    """ADR-061: run the InterviewCoach for one chosen scored job on demand.

    Synchronous; ~10-20s. Persists the prep so it appears in the interview
    readiness section.
    """
    r = httpx.post(
        f"{BASE_URL}/workflows/{workflow_id}/jobs/{job_id}/interview-prep",
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


def submit_tailoring_decision(tailoring_id: str, approval: str,
                              edited: dict | None = None) -> dict:
    """approval in {approve, revise, reject, edit}.

    For an edit, pass the human-authored draft in `edited`; it is recorded as the
    final, owner-authored version (not re-reviewed). POSTs to the decisions
    collection on the tailoring.
    """
    payload: dict = {"approval": approval}
    if edited is not None:
        payload["edited"] = edited
    r = httpx.post(
        f"{BASE_URL}/tailorings/{tailoring_id}/decisions",
        json=payload,
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
