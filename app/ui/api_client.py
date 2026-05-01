"""Thin httpx wrapper for write actions against the FastAPI backend.

All control-path actions (start workflow, submit HITL decisions, fetch report)
go through this module. Read-only browse views go through db_reader.py instead.
"""
from __future__ import annotations

import httpx

BASE_URL = "http://localhost:8000"
_TIMEOUT_GET = 5.0
_TIMEOUT_POST = 10.0


def start_workflow(
    resume_id: str,
    search_criteria: dict,
    workflow_type: str = "full_career_review",
    effective_config: dict | None = None,
) -> dict:
    r = httpx.post(
        f"{BASE_URL}/workflows",
        json={
            "resume_id": resume_id,
            "search_criteria": search_criteria,
            "workflow_type": workflow_type,
            "effective_config": effective_config or {},
        },
        timeout=_TIMEOUT_POST,
    )
    r.raise_for_status()
    return r.json()


def get_workflow_status(workflow_id: str) -> dict:
    r = httpx.get(f"{BASE_URL}/workflows/{workflow_id}", timeout=_TIMEOUT_GET)
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
