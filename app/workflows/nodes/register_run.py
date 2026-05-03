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

logger = logging.getLogger(__name__)


def make_register_run_node(workflow_repo: WorkflowRepository) -> Callable[[dict], dict]:
    def register_run(state: dict) -> dict:
        workflow_id = state.get("workflow_id", "")
        if not workflow_id:
            logger.warning("register_run: state has no workflow_id; skipping persist")
            return {"current_step": "registered", "updated_at": utcnow_iso()}

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

        return {"current_step": "registered", "updated_at": utcnow_iso()}

    return register_run
