"""await_scoring_selection node — phase-1 terminal in manual-selection mode (ADR-060).

When effective_config['scoring']['manual_selection'] is on, the graph discovers a
wide net of jobs and then stops here, WITHOUT calling interrupt(). It persists the
discovered (unscored) jobs to workflow_runs so the UI can list them and the
phase-2 trigger can read them, then sets status=awaiting_scoring_selection and
ends. The user picks jobs via the UI; POST /workflows/{wf}/scoring re-enters the
same graph at score_jobs (phase="scoring") with only the selected subset.

This is curate-before-scoring: a human filter placed for cost/relevance, not an
irreversible-action gate. It adds no interrupt() machinery (ADR-059 stands).
"""
from __future__ import annotations

import logging
from typing import Callable

from app.repositories.database import utcnow_iso
from app.repositories.workflow_repository import WorkflowRepository

logger = logging.getLogger(__name__)


def make_await_scoring_selection_node(
    workflow_repo: WorkflowRepository | None = None,
) -> Callable[[dict], dict]:
    def await_scoring_selection(state: dict) -> dict:
        workflow_id: str = state.get("workflow_id", "")
        normalized_jobs = state.get("normalized_jobs") or []

        update = {
            "status": "awaiting_scoring_selection",
            "current_step": "awaiting_scoring_selection",
            "updated_at": utcnow_iso(),
        }

        # Persist discovered-but-unscored jobs to workflow_runs so the UI read
        # path (db_reader) and the phase-2 trigger can both see them. The
        # langgraph checkpoint also holds them, but the UI reads workflow_runs.
        if workflow_repo is not None and workflow_id:
            try:
                workflow_repo.update_state(workflow_id, {**state, **update})
            except Exception as exc:
                logger.warning(
                    "await_scoring_selection: workflow_runs update failed for %s: %s",
                    workflow_id, exc,
                )

        logger.info(
            "await_scoring_selection: %s discovered jobs await user selection (wf=%s)",
            len(normalized_jobs), workflow_id,
        )
        return update

    return await_scoring_selection
