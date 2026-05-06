"""register_run node — first node; persists the workflow row to workflow_runs.

Captures the initial state snapshot (search_criteria, custom_urls, effective_config)
so the Workflow Detail screen can show "Settings used for this run" later.
The langgraph SqliteSaver writes its own checkpoints table for resumption, but
that is opaque to UI queries; this row gives us a clean queryable record.
"""
from __future__ import annotations

import logging
from typing import Callable

from app.repositories.database import utcnow_iso
from app.repositories.workflow_repository import WorkflowRepository
from app.services.observability_service import ObservabilityService

logger = logging.getLogger(__name__)


def make_register_run_node(
    workflow_repo: WorkflowRepository,
    observability: ObservabilityService | None = None,
) -> Callable[[dict], dict]:
    def register_run(state: dict) -> dict:
        workflow_id = state.get("workflow_id", "")
        started_at = utcnow_iso()
        if not workflow_id:
            logger.warning("register_run: state has no workflow_id; skipping persist")
            return {"current_step": "registered", "updated_at": started_at}

        existing = None
        try:
            existing = workflow_repo.get_by_id(workflow_id)
        except Exception as exc:
            logger.warning("register_run: get_by_id failed for %s: %s", workflow_id, exc)

        if existing is None:
            try:
                workflow_repo.create(
                    workflow_id,
                    state.get("workflow_type", "full_career_review"),
                    state,
                )
                logger.info("register_run: persisted workflow_runs row for %s", workflow_id)
            except Exception as exc:
                # Non-fatal: the run can still proceed; only UI/history is impacted.
                logger.warning("register_run: persist failed for %s: %s", workflow_id, exc)

            # ADR-pending observability fix: initialise the run_metrics row up front
            # so finalize_run_metrics in generate_report can update it. Previously
            # this call existed only in tests, so run_metrics stayed empty in prod.
            if observability is not None:
                observability.init_run_metrics(workflow_id, started_at)

        return {"current_step": "registered", "updated_at": started_at}

    return register_run
