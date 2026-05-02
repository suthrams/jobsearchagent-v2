"""Workflow router — POST /workflows, GET /workflows/{id}, POST /workflows/{id}/decisions."""
from __future__ import annotations

import concurrent.futures
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from langgraph.types import Command
from pydantic import TypeAdapter, ValidationError

try:
    from langgraph.errors import GraphInterrupt as _GraphInterrupt  # langgraph >= 0.2.x
except ImportError:  # older builds expose it on langgraph.types or not at all
    _GraphInterrupt = Exception  # type: ignore[assignment,misc]

from app.api.dependencies import get_graph
from app.api.schemas.requests import (
    DecisionRequest,
    JobSelectionDecision,
    StartWorkflowRequest,
    TailoringDecision,
)
from app.api.schemas.responses import WorkflowStatusResponse
from app.repositories.database import utcnow_iso
from app.workflows.limits import MAX_SELECTED_JOBS

_decision_adapter = TypeAdapter(DecisionRequest)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workflows", tags=["workflows"])

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def _build_initial_state(req: StartWorkflowRequest, workflow_id: str) -> dict:
    now = utcnow_iso()
    return {
        "workflow_id": workflow_id,
        "workflow_type": req.workflow_type,
        "status": "running",
        "current_step": "initialized",
        "user_id": None,
        "resume_id": req.resume_id,
        "resume_profile": None,
        "resume_version": None,
        "search_criteria": req.search_criteria,
        "raw_jobs": [],
        "normalized_jobs": [],
        "scored_jobs": [],
        "selected_jobs": [],
        "research_context": None,
        "skill_gaps": {},
        "review_rounds": [],
        "final_resume_review": None,
        "career_advice": None,
        "interview_prep": None,
        "tailored_resume": None,
        "fidelity_review": None,
        "pending_decision": None,
        "human_decisions": [],
        "report": None,
        "run_metrics": {
            "llm_calls": 0,
            "tokens_input": 0,
            "tokens_output": 0,
            "estimated_cost_usd": 0.0,
            "total_duration_ms": 0,
            "started_at": now,
            "completed_at": None,
        },
        "errors": [],
        "effective_config": req.effective_config,
        "created_at": now,
        "updated_at": now,
        "user_requested_interview_prep": False,
        "user_requested_tailoring": False,
    }


def _run_graph(graph, initial_state: dict, config: dict) -> None:
    """Execute graph.invoke() in thread pool. GraphInterrupt is normal — not an error."""
    try:
        graph.invoke(initial_state, config)
    except _GraphInterrupt:
        logger.debug("Graph paused at HITL interrupt for thread %s", config)
    except Exception as exc:
        logger.exception("Unhandled error in graph thread for config %s", config)
        try:
            from app.repositories.database import utcnow_iso
            graph.update_state(config, {
                "status": "failed",
                "errors": [{"stage": "graph", "error_type": type(exc).__name__, "message": str(exc), "recoverable": False}],
                "updated_at": utcnow_iso(),
            })
        except Exception:
            logger.exception("Failed to write failed status to graph state for %s", config)


def _resume_graph(graph, decision_payload: dict, config: dict) -> None:
    """Resume a paused graph with a human decision."""
    try:
        graph.invoke(Command(resume=decision_payload), config)
    except _GraphInterrupt:
        logger.debug("Graph paused again at HITL interrupt for thread %s", config)
    except Exception as exc:
        logger.exception("Unhandled error resuming graph for config %s", config)
        try:
            from app.repositories.database import utcnow_iso
            graph.update_state(config, {
                "status": "failed",
                "errors": [{"stage": "graph_resume", "error_type": type(exc).__name__, "message": str(exc), "recoverable": False}],
                "updated_at": utcnow_iso(),
            })
        except Exception:
            logger.exception("Failed to write failed status to graph state for %s", config)


def _read_status(graph, workflow_id: str) -> WorkflowStatusResponse | None:
    """Read current workflow status from graph checkpoint. Returns None if not found."""
    config = {"configurable": {"thread_id": workflow_id}}
    snapshot = graph.get_state(config)

    if snapshot is None or not snapshot.values:
        return None

    state = snapshot.values

    # Determine status from interrupt / next / state
    pending_decision: dict | None = None
    status: str

    # Check for active interrupts (waiting_for_user)
    has_interrupts = any(
        getattr(task, "interrupts", None)
        for task in (snapshot.tasks or [])
    )
    if has_interrupts:
        status = "waiting_for_user"
        # Extract interrupt payload from first task that has interrupts
        for task in snapshot.tasks:
            interrupts = getattr(task, "interrupts", None)
            if interrupts:
                pending_decision = interrupts[0].value
                break
    elif snapshot.next:
        status = "running"
    else:
        status = state.get("status", "completed")

    return WorkflowStatusResponse(
        workflow_id=workflow_id,
        status=status,
        current_step=state.get("current_step"),
        pending_decision=pending_decision,
        run_metrics=state.get("run_metrics"),
        errors=state.get("errors") or [],
        updated_at=state.get("updated_at"),
    )


@router.post("", status_code=202)
def start_workflow(
    body: StartWorkflowRequest,
    graph=Depends(get_graph),
) -> dict:
    """Start a new workflow. Returns 202 immediately; execution is async in thread pool."""
    workflow_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": workflow_id}}
    initial_state = _build_initial_state(body, workflow_id)

    _executor.submit(_run_graph, graph, initial_state, config)

    logger.info("Workflow %s submitted to thread pool.", workflow_id)
    return {
        "workflow_id": workflow_id,
        "status": "running",
        "created_at": initial_state["created_at"],
    }


@router.get("/{workflow_id}", response_model=WorkflowStatusResponse)
def get_workflow_status(
    workflow_id: str,
    graph=Depends(get_graph),
) -> WorkflowStatusResponse:
    """Return current status for a workflow."""
    status = _read_status(graph, workflow_id)
    if status is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "workflow_not_found",
                "message": f"Workflow {workflow_id!r} not found.",
                "workflow_id": workflow_id,
            },
        )
    return status


@router.post("/{workflow_id}/decisions", status_code=202)
async def submit_decision(
    workflow_id: str,
    request: Request,
    graph=Depends(get_graph),
) -> dict:
    """Submit a HITL decision to resume a paused workflow. Returns 202 immediately."""
    # Parse and validate the request body using the discriminated union adapter.
    # This surfaces Pydantic validation errors as 422 before any business logic runs.
    raw = await request.json()
    try:
        body = _decision_adapter.validate_python(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    config = {"configurable": {"thread_id": workflow_id}}

    # 1. Workflow exists?
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

    # 2. Has active interrupts (waiting_for_user)?
    has_interrupts = any(
        getattr(task, "interrupts", None)
        for task in (snapshot.tasks or [])
    )
    if not has_interrupts:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "workflow_not_paused",
                "message": "Workflow is not waiting for a user decision.",
                "workflow_id": workflow_id,
            },
        )

    # 3. decision_type matches interrupt payload?
    interrupt_payload: dict = {}
    for task in snapshot.tasks:
        interrupts = getattr(task, "interrupts", None)
        if interrupts:
            interrupt_payload = interrupts[0].value or {}
            break

    expected_type = interrupt_payload.get("decision_type")
    if body.decision_type != expected_type:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "decision_type_mismatch",
                "message": (
                    f"Expected decision_type {expected_type!r}, "
                    f"got {body.decision_type!r}."
                ),
                "workflow_id": workflow_id,
            },
        )

    # 4 & 5. Validate job selection specifics
    if isinstance(body, JobSelectionDecision):
        eligible_jobs = interrupt_payload.get("eligible_jobs", [])
        eligible_ids = {j.get("job_id") for j in eligible_jobs}
        invalid = [jid for jid in body.selected_job_ids if jid not in eligible_ids]
        if invalid:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_job_ids",
                    "message": f"Job IDs not in eligible set: {invalid}",
                    "workflow_id": workflow_id,
                },
            )
        if len(body.selected_job_ids) > MAX_SELECTED_JOBS:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "too_many_jobs_selected",
                    "message": (
                        f"Cannot select more than {MAX_SELECTED_JOBS} jobs. "
                        f"Got {len(body.selected_job_ids)}."
                    ),
                    "workflow_id": workflow_id,
                },
            )
        decision_payload = {"selected_job_ids": body.selected_job_ids}
    elif isinstance(body, TailoringDecision):
        decision_payload = {"decision_value": body.approval}
    else:
        decision_payload = {}

    _executor.submit(_resume_graph, graph, decision_payload, config)

    logger.info(
        "Decision %r submitted for workflow %s, resuming in thread pool.",
        body.decision_type,
        workflow_id,
    )
    return {"workflow_id": workflow_id, "status": "running"}
