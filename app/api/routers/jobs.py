"""Jobs router — GET /workflows/{id}/jobs and the per-job exclusion surface (ADR-057)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.dependencies import get_deps, get_graph
from app.api.schemas.responses import JobSummaryResponse
from app.workflows.workflow_graph import WorkflowDependencies

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workflows", tags=["jobs"])

# Second router for top-level /jobs/* endpoints. Kept separate from the
# workflow-scoped router above so the prefix can be different.
exclusion_router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{workflow_id}/jobs")
def list_workflow_jobs(
    workflow_id: str,
    graph=Depends(get_graph),
) -> dict:
    """Return all scored jobs for a workflow."""
    config = {"configurable": {"thread_id": workflow_id}}
    snapshot = graph.get_state(config)

    if snapshot is None or not snapshot.values:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "workflow_not_found",
                "message": f"Workflow {workflow_id!r} not found.",
                "workflow_id": workflow_id,
            },
        )

    scored_jobs: list[dict] = snapshot.values.get("scored_jobs") or []

    jobs = [
        JobSummaryResponse(
            job_id=j.get("job_id", j.get("id", "")),
            title=j.get("title", ""),
            company=j.get("company", ""),
            status=j.get("status", "unknown"),
            overall_score=j.get("overall_score"),
            technical_score=j.get("technical_score"),
            architecture_score=j.get("architecture_score"),
            leadership_score=j.get("leadership_score"),
            domain_score=j.get("domain_score"),
            strengths=j.get("strengths") or [],
            gaps=j.get("gaps") or [],
            recommended_next_action=j.get("recommended_next_action"),
        )
        for j in scored_jobs
    ]

    return {"workflow_id": workflow_id, "jobs": [j.model_dump() for j in jobs]}


# ── ADR-057: per-job exclusion ───────────────────────────────────────────────
# Pipeline filter, NOT application tracking. See ADR-057 for the
# filter-vs-tracker distinction. We intentionally do NOT capture
# applied_at, status transitions, recruiter info, or any outcome metadata.

class ExcludeRequest(BaseModel):
    reason: str | None = None


@exclusion_router.post("/{job_id}/exclude")
def exclude_job(
    job_id: str,
    body: ExcludeRequest | None = None,
    deps: WorkflowDependencies = Depends(get_deps),
) -> dict:
    """Mark a job excluded. Filtered out of discovery + analytics views.

    Idempotent — calling twice on the same job_id is a no-op (the second
    call overwrites the reason).
    """
    if deps.job_repo.get_by_id(job_id) is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "job_not_found",
                "message": f"Job {job_id!r} not found.",
                "job_id": job_id,
            },
        )
    deps.job_repo.set_excluded(job_id, (body.reason if body else None))
    return {"job_id": job_id, "excluded": True}


@exclusion_router.delete("/{job_id}/exclude")
def unexclude_job(
    job_id: str,
    deps: WorkflowDependencies = Depends(get_deps),
) -> dict:
    """Un-exclude a previously excluded job. Rare; provided for completeness."""
    if deps.job_repo.get_by_id(job_id) is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "job_not_found",
                "message": f"Job {job_id!r} not found.",
                "job_id": job_id,
            },
        )
    deps.job_repo.clear_excluded(job_id)
    return {"job_id": job_id, "excluded": False}


@exclusion_router.get("/excluded")
def list_excluded_jobs(
    deps: WorkflowDependencies = Depends(get_deps),
) -> dict:
    """List all currently excluded jobs, newest exclusion first."""
    rows = deps.job_repo.list_excluded()
    return {
        "count": len(rows),
        "jobs": [
            {
                "job_id":          r["id"],
                "title":           r.get("title"),
                "company":         r.get("company"),
                "url":             r.get("url"),
                "excluded_reason": r.get("excluded_reason"),
                "excluded_at":     r.get("excluded_at"),
            }
            for r in rows
        ],
    }
