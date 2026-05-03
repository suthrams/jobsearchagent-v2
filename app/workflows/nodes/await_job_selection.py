"""auto_select_jobs node — selects high-scoring jobs for deep review without HITL.

Replaces the previous HITL pause. Eligibility = any track score (technical,
architecture, leadership) at or above effective_config['scoring']['min_match_score'].
Up to MAX_SELECTED_JOBS top-qualifying jobs proceed; if none qualify, the
workflow continues with selected_jobs=[] and downstream nodes short-circuit.
"""
from __future__ import annotations

import logging
from typing import Callable

from app.repositories.database import utcnow_iso
from app.workflows.limits import (
    MAX_SELECTED_JOBS,
    best_track_score,
    get_min_match_score,
    qualifies_for_deep_review,
)

logger = logging.getLogger(__name__)


def make_await_job_selection_node() -> Callable[[dict], dict]:
    """Factory kept for backwards-compat with workflow_graph wiring."""
    def auto_select_jobs(state: dict) -> dict:
        scored_jobs: list[dict] = state.get("scored_jobs") or []
        threshold = get_min_match_score(state)

        eligible = [
            j for j in scored_jobs
            if j.get("status") == "scored" and qualifies_for_deep_review(j, threshold)
        ]
        eligible.sort(key=best_track_score, reverse=True)
        selected_jobs = eligible[:MAX_SELECTED_JOBS]

        human_decisions = list(state.get("human_decisions") or [])
        human_decisions.append({
            "decision_type": "auto_select_jobs_for_deep_review",
            "decision_value": "auto",
            "payload": {
                "threshold": threshold,
                "eligible_count": len(eligible),
                "selected_job_ids": [
                    j.get("job_id", j.get("id", "")) for j in selected_jobs
                ],
            },
            "decided_at": utcnow_iso(),
        })

        logger.info(
            "auto_select_jobs: threshold=%d eligible=%d selected=%d",
            threshold, len(eligible), len(selected_jobs),
        )

        return {
            "selected_jobs": selected_jobs,
            "pending_decision": None,
            "human_decisions": human_decisions,
            "current_step": "deep_review_in_progress" if selected_jobs else "no_qualifying_jobs",
            "updated_at": utcnow_iso(),
        }

    return auto_select_jobs
