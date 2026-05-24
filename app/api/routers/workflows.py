"""Workflow router — POST /workflows, GET /workflows/{id}, status + retry."""
from __future__ import annotations

import concurrent.futures
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_graph
from app.api.schemas.requests import StartWorkflowRequest
from app.api.schemas.responses import WorkflowStatusResponse
from app.providers.model_registry import (
    HIGH_VOLUME_AGENTS,
    HIGH_VOLUME_SAFE_MODELS,
    ModelConfigError,
    catalog_from_config,
    defaults_from_config,
    known_models_from_catalog,
)
from app.repositories.database import utcnow_iso
from app.services.config_service import ConfigService

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
        "custom_urls": list(req.custom_urls or []),
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
    }


def _run_graph(graph, initial_state: dict, config: dict) -> None:
    """Execute graph.invoke() in thread pool."""
    try:
        graph.invoke(initial_state, config)
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


def _retry_graph(graph, config: dict) -> None:
    """Re-invoke a workflow from its last checkpoint after a server restart."""
    try:
        graph.invoke(None, config)
    except Exception as exc:
        logger.exception("Unhandled error retrying graph for config %s", config)
        try:
            graph.update_state(config, {
                "status": "failed",
                "errors": [{"stage": "graph_retry", "error_type": type(exc).__name__, "message": str(exc), "recoverable": False}],
                "updated_at": utcnow_iso(),
            })
        except Exception:
            logger.exception("Failed to write failed status after retry for %s", config)


def _read_status(graph, workflow_id: str) -> WorkflowStatusResponse | None:
    """Read current workflow status from graph checkpoint. Returns None if not found."""
    config = {"configurable": {"thread_id": workflow_id}}
    snapshot = graph.get_state(config)

    if snapshot is None or not snapshot.values:
        return None

    state = snapshot.values

    if snapshot.next:
        status = "running"
    else:
        status = state.get("status", "completed")

    return WorkflowStatusResponse(
        workflow_id=workflow_id,
        status=status,
        current_step=state.get("current_step"),
        run_metrics=state.get("run_metrics"),
        errors=state.get("errors") or [],
        updated_at=state.get("updated_at"),
    )


@router.post("", status_code=202)
def start_workflow(
    body: StartWorkflowRequest,
    graph=Depends(get_graph),
) -> dict:
    """Start a new workflow. Returns 202 immediately; execution is async in thread pool.

    Per ADR-058, snapshots the effective per-agent assignment into the run state
    so the workflow_runs row records exactly which (provider, model) ran each
    agent. Accepts an optional `agent_overrides` map to vary the assignment for
    this run only; overrides are validated against the catalog + cost cap.

    Phase 1 note: overrides are persisted in the snapshot but the runtime
    agents still resolve through the global registry. The response includes a
    `warnings` array surfacing this gap when overrides are supplied.
    """
    warnings: list[str] = []
    snapshot_agents, override_warnings = _resolve_agent_snapshot(body.agent_overrides)
    warnings.extend(override_warnings)

    # Merge the agents snapshot into the incoming effective_config so register_run
    # writes the full picture to workflow_runs.
    effective_with_agents = dict(body.effective_config or {})
    effective_with_agents["agents"] = snapshot_agents
    body = body.model_copy(update={"effective_config": effective_with_agents})

    workflow_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": workflow_id}}
    initial_state = _build_initial_state(body, workflow_id)

    _executor.submit(_run_graph, graph, initial_state, config)

    logger.info("Workflow %s submitted to thread pool.", workflow_id)
    return {
        "workflow_id": workflow_id,
        "status": "running",
        "created_at": initial_state["created_at"],
        "agent_assignment": snapshot_agents,
        "warnings": warnings,
    }


def _resolve_agent_snapshot(
    overrides: dict[str, dict[str, str]],
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Compute the per-workflow agent assignment snapshot.

    Reads the global effective config (YAML defaults + user_config overrides)
    via ConfigService, then layers the kickoff `overrides` on top after
    validation. Returns (snapshot, warnings). Raises HTTPException on invalid
    override input.

    Tolerates a config without the `agents:` / `models:` blocks (returns an
    empty snapshot and a warning). Lets older configs and test environments
    keep working until they are migrated to ADR-058 shape.
    """
    eff = ConfigService().get_effective_config()
    try:
        catalog = catalog_from_config(eff)
        defaults = defaults_from_config(eff)
    except ModelConfigError as exc:
        if overrides:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "config_missing_agents_or_models",
                    "message": (
                        "agent_overrides was supplied but config.yaml is missing "
                        f"the required block: {exc}. Add the agents and models "
                        "blocks per config.example.yaml."
                    ),
                },
            )
        logger.info(
            "_resolve_agent_snapshot: config missing agents/models block (%s); "
            "starting without an agent snapshot. Update config.yaml per "
            "config.example.yaml to enable per-workflow snapshot.", exc,
        )
        return {}, ["agent assignment snapshot unavailable: config.yaml is missing "
                    "the `agents:` and/or `models:` blocks (see config.example.yaml)."]
    known_models = known_models_from_catalog(catalog)

    snapshot: dict[str, dict[str, str]] = {a: dict(v) for a, v in defaults.items()}
    warnings: list[str] = []

    if not overrides:
        return snapshot, warnings

    for agent_name, pick in overrides.items():
        if agent_name not in defaults:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "unknown_agent",
                    "message": f"Unknown agent {agent_name!r}. Known: {sorted(defaults)}",
                },
            )
        if not isinstance(pick, dict):
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_override",
                        "message": f"agent_overrides.{agent_name} must be {{provider, model}}"},
            )
        provider = pick.get("provider")
        model = pick.get("model")
        if not provider or not model:
            raise HTTPException(
                status_code=422,
                detail={"error": "incomplete_assignment",
                        "message": "Both provider and model are required."},
            )
        if provider not in known_models:
            raise HTTPException(
                status_code=422,
                detail={"error": "unknown_provider",
                        "message": f"Unknown provider {provider!r}. Known: {sorted(known_models)}"},
            )
        if model not in known_models[provider]:
            raise HTTPException(
                status_code=422,
                detail={"error": "unknown_model",
                        "message": f"Unknown model {model!r} for provider {provider!r}. "
                                   f"Known: {known_models[provider]}"},
            )
        if agent_name in HIGH_VOLUME_AGENTS and model not in HIGH_VOLUME_SAFE_MODELS:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "cost_cap_violation",
                    "message": (
                        f"Agent {agent_name!r} is high-volume and cannot be assigned "
                        f"{model!r}. Allowed models: {sorted(HIGH_VOLUME_SAFE_MODELS)}."
                    ),
                },
            )
        snapshot[agent_name] = {"provider": str(provider), "model": str(model)}

    warnings.append(
        "agent_overrides accepted and persisted to the workflow snapshot, but per-run "
        "runtime agent swap is Phase 9 (ADR-058). Agents in this run will still "
        "resolve through the global registry assignment."
    )
    return snapshot, warnings


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


@router.post("/{workflow_id}/retry", status_code=202)
def retry_workflow(
    workflow_id: str,
    graph=Depends(get_graph),
) -> dict:
    """Re-submit a workflow that was interrupted by a server restart.

    Valid only when snapshot.next is non-empty (there are pending steps to resume).
    """
    config = {"configurable": {"thread_id": workflow_id}}
    snapshot = graph.get_state(config)
    if snapshot is None or not snapshot.values:
        raise HTTPException(
            status_code=404,
            detail={"error": "workflow_not_found", "message": f"Workflow {workflow_id!r} not found.", "workflow_id": workflow_id},
        )

    if not snapshot.next:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "workflow_already_complete",
                "message": "Workflow has no pending steps to resume.",
                "workflow_id": workflow_id,
            },
        )

    _executor.submit(_retry_graph, graph, config)
    logger.info("Workflow %s re-submitted for retry, resuming from: %s", workflow_id, list(snapshot.next))
    return {"workflow_id": workflow_id, "status": "running", "resuming_from": list(snapshot.next)}


