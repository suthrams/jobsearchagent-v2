"""await_job_selection node — HITL pause #1: user selects jobs for deep review."""
from __future__ import annotations

import logging
from typing import Callable

from langgraph.types import interrupt

from app.repositories.database import utcnow_iso
from app.workflows.limits import MAX_SELECTED_JOBS

logger = logging.getLogger(__name__)


def make_await_job_selection_node() -> Callable[[dict], dict]:
    def await_job_selection(state: dict) -> dict:
        scored_jobs: list[dict] = state.get("scored_jobs") or []
        eligible = [j for j in scored_jobs if j.get("status") == "scored"]

        decision = interrupt({
            "decision_type": "select_jobs_for_deep_review",
            "message": f"Select up to {MAX_SELECTED_JOBS} jobs to move into deep review.",
            "eligible_jobs": [
                {
                    "job_id": j.get("job_id", j.get("id", "")),
                    "title": j.get("title", ""),
                    "company": j.get("company", ""),
                    "overall_score": j.get("overall_score", 0),
                }
                for j in eligible
            ],
        })

        selected_ids: list[str] = (decision or {}).get("selected_job_ids", [])
        selected_ids = selected_ids[:MAX_SELECTED_JOBS]

        selected_jobs = [
            j for j in eligible
            if j.get("job_id", j.get("id", "")) in selected_ids
        ]

        human_decisions = list(state.get("human_decisions") or [])
        human_decisions.append({
            "decision_type": "select_jobs_for_deep_review",
            "decision_value": "selected",
            "payload": {"selected_job_ids": selected_ids},
            "decided_at": utcnow_iso(),
        })

        logger.info("await_job_selection: user selected %d jobs", len(selected_jobs))

        return {
            "selected_jobs": selected_jobs,
            "pending_decision": None,
            "human_decisions": human_decisions,
            "current_step": "deep_review_in_progress",
            "updated_at": utcnow_iso(),
        }

    return await_job_selection
