"""Jobs router — GET /workflows/{id}/jobs."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_graph
from app.api.schemas.responses import JobSummaryResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workflows", tags=["jobs"])


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
