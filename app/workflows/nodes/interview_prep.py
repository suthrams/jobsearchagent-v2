"""interview_prep node — InterviewCoach for high-match or user-requested jobs."""
from __future__ import annotations

import logging
import uuid
from typing import Callable

from app.agents.interview_coach import InterviewCoach
from app.providers.llm_client import LLMProviderError
from app.repositories.advice_repository import AdviceRepository
from app.repositories.database import utcnow_iso
from app.services.observability_service import ObservabilityService
from app.workflows.limits import (
    add_llm_call,
    append_error,
    best_track_score,
    get_metrics,
    get_min_match_score,
    safe_agent_usage,
)

logger = logging.getLogger(__name__)


def make_interview_prep_node(
    interview_coach: InterviewCoach,
    advice_repo: AdviceRepository,
    observability: ObservabilityService,
) -> Callable[[dict], dict]:
    def interview_prep(state: dict) -> dict:
        workflow_id: str = state.get("workflow_id", "")
        resume_id: str = state.get("resume_id") or ""
        resume_profile: dict = state.get("resume_profile") or {}
        selected_jobs: list[dict] = state.get("selected_jobs") or []
        scored_jobs: list[dict] = state.get("scored_jobs") or []
        career_advice: dict | None = state.get("career_advice")
        final_review: dict | None = state.get("final_resume_review")

        metrics = get_metrics(state)
        errors = list(state.get("errors") or [])

        if not selected_jobs:
            return {"current_step": "interview_prep", "updated_at": utcnow_iso()}

        top_job = max(selected_jobs, key=best_track_score)
        job_id = top_job.get("job_id", top_job.get("id", ""))
        threshold = get_min_match_score(state)

        # Routing guard — this node only runs when the router decided to call it,
        # but we keep the check here as a safety net.
        if best_track_score(top_job) < threshold and not state.get("user_requested_interview_prep"):
            return {"current_step": "interview_prep", "updated_at": utcnow_iso()}

        score_by_job = {sj.get("job_id", sj.get("id", "")): sj for sj in scored_jobs}
        job_score = score_by_job.get(job_id, {})

        try:
            prep = interview_coach.run(workflow_id, {
                "job_id": job_id,
                "job_description": top_job.get("job_description", ""),
                "resume_profile": resume_profile,
                "job_score": job_score,
                "research_context": {},
                "career_advice": career_advice or {},
                "final_review": final_review or {},
            })
            _ti, _to, _cost = safe_agent_usage(interview_coach)
            metrics = add_llm_call(metrics, tokens_in=_ti, tokens_out=_to, cost_usd=_cost)
        except LLMProviderError as exc:
            logger.warning("interview_prep: failed for %s: %s", job_id, exc)
            errors = append_error({"errors": errors}, "interview_prep", "coach_failed",
                                  str(exc), recoverable=True)
            return {
                "interview_prep": None,
                "run_metrics": metrics,
                "errors": errors,
                "current_step": "interview_prep",
                "updated_at": utcnow_iso(),
            }

        prep_dict = prep.model_dump()
        try:
            advice_repo.create_prep(str(uuid.uuid4()), workflow_id, job_id, prep_dict)
        except Exception as exc:
            logger.warning("interview_prep: persist failed: %s", exc)

        return {
            "interview_prep": prep_dict,
            "run_metrics": metrics,
            "errors": errors,
            "current_step": "interview_prep",
            "updated_at": utcnow_iso(),
        }

    return interview_prep
