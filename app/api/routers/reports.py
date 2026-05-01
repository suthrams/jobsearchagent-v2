"""Reports router — GET /workflows/{id}/report."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_graph
from app.api.schemas.responses import ReportResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workflows", tags=["reports"])


@router.get("/{workflow_id}/report", response_model=ReportResponse)
def get_workflow_report(
    workflow_id: str,
    graph=Depends(get_graph),
) -> ReportResponse:
    """Return the generated report for a completed workflow."""
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

    state = snapshot.values
    status = state.get("status", "")

    if status != "completed":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "workflow_not_completed",
                "message": (
                    f"Workflow {workflow_id!r} is not completed (status={status!r}). "
                    "Wait for the workflow to finish before fetching the report."
                ),
                "workflow_id": workflow_id,
            },
        )

    report = state.get("report") or {}

    return ReportResponse(workflow_id=workflow_id, report=report)
