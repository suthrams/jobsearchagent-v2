"""Run-scoped read endpoints (ADR-075 Phases 4-6).

DB-backed reads for a single workflow run, moved off the UI's direct-SQLite path
(db_reader) into the API. Thin wrappers over app/services/reads/workflow_reads.
Resource-oriented sub-resources of /workflows/{id} (not a UI/BFF router, ADR-075
§B). Distinct from jobs.py's GET /workflows/{id}/jobs, which reads the live
LangGraph state snapshot; these read the persisted tables.

Registered before the workflows router so GET /workflows/recent (literal) matches
ahead of GET /workflows/{workflow_id}.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.schemas.responses import DictList
from app.services.reads import workflow_reads as wr

router = APIRouter(prefix="/workflows", tags=["reads"])


@router.get("/recent", response_model=DictList)
def recent_workflows() -> DictList:
    """Recent runs from checkpoints (monitor reconnect list)."""
    return DictList(**wr.list_recent_workflows())


@router.get("/{workflow_id}/scored-jobs", response_model=DictList)
def scored_jobs(workflow_id: str, include_excluded: bool = True) -> DictList:
    """Persisted scored jobs for a run, with per-track scores + pipeline pointers
    (ADR-057: excluded shown by default for the per-run Find & Score view)."""
    return DictList(**wr.list_workflow_jobs(workflow_id, include_excluded=include_excluded))


@router.get("/{workflow_id}/reviews", response_model=DictList)
def reviews(workflow_id: str) -> DictList:
    return DictList(**wr.list_deep_review_results(workflow_id))


@router.get("/{workflow_id}/interview-prep", response_model=DictList)
def interview_prep(workflow_id: str) -> DictList:
    return DictList(**wr.list_interview_prep(workflow_id))


@router.get("/{workflow_id}/steps", response_model=DictList)
def steps(workflow_id: str) -> DictList:
    return DictList(**wr.list_step_executions(workflow_id))


@router.get("/{workflow_id}/agent-events", response_model=DictList)
def agent_events(workflow_id: str) -> DictList:
    return DictList(**wr.list_agent_events(workflow_id))


@router.get("/{workflow_id}/llm-calls", response_model=DictList)
def llm_calls(workflow_id: str) -> DictList:
    return DictList(**wr.list_llm_calls(workflow_id))


@router.get("/{workflow_id}/jobs/{job_id}/pipeline")
def job_pipeline(workflow_id: str, job_id: str) -> dict:
    """All persisted outputs for one (run, job) pair — the Job Detail drill-down."""
    return wr.get_job_pipeline(workflow_id, job_id)


@router.get("/{workflow_id}/detail")
def workflow_detail(workflow_id: str) -> dict:
    """The persisted workflow_runs row + parsed state (Workflow Detail header)."""
    rec = wr.get_workflow_run_detail(workflow_id)
    if rec is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "workflow_not_found",
                    "message": f"Workflow {workflow_id!r} not found.",
                    "workflow_id": workflow_id},
        )
    return rec
