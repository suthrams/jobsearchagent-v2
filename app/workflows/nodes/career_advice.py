"""career_advice node — CareerAdvisor for the top selected job."""
from __future__ import annotations

import logging
import uuid
from typing import Callable

from app.agents.career_advisor import CareerAdvisor
from app.providers.llm_client import LLMProviderError
from app.repositories.advice_repository import AdviceRepository
from app.repositories.database import utcnow_iso
from app.services.observability_service import ObservabilityService
from app.workflows.limits import add_llm_call, append_error, get_metrics, safe_agent_usage

logger = logging.getLogger(__name__)


def make_career_advice_node(
    career_advisor: CareerAdvisor,
    advice_repo: AdviceRepository,
    observability: ObservabilityService,
) -> Callable[[dict], dict]:
    def career_advice(state: dict) -> dict:
        workflow_id: str = state.get("workflow_id", "")
        resume_id: str = state.get("resume_id") or ""
        resume_profile: dict = state.get("resume_profile") or {}
        selected_jobs: list[dict] = state.get("selected_jobs") or []
        scored_jobs: list[dict] = state.get("scored_jobs") or []
        final_review: dict | None = state.get("final_resume_review")
        effective_config: dict = state.get("effective_config") or {}
        career_track: str = effective_config.get("scoring", {}).get("career_track", "ic")

        metrics = get_metrics(state)
        errors = list(state.get("errors") or [])

        if not selected_jobs:
            return {
                "career_advice": None,
                "current_step": "career_advice",
                "updated_at": utcnow_iso(),
            }

        # Use the highest-scoring selected job
        top_job = max(selected_jobs, key=lambda j: j.get("overall_score", 0))
        job_id = top_job.get("job_id", top_job.get("id", ""))
        job_desc = top_job.get("job_description", "")

        # Find the score dict for context
        score_by_job = {sj.get("job_id", sj.get("id", "")): sj for sj in scored_jobs}
        job_score = score_by_job.get(job_id, {})

        try:
            advice = career_advisor.run(workflow_id, {
                "job_id": job_id,
                "resume_id": resume_id,
                "job_description": job_desc,
                "resume_profile": resume_profile,
                "final_review": final_review or {},
                "job_score": job_score,
                "career_track": career_track,
            })
            _ti, _to, _cost = safe_agent_usage(career_advisor)
            metrics = add_llm_call(metrics, tokens_in=_ti, tokens_out=_to, cost_usd=_cost)
        except LLMProviderError as exc:
            logger.warning("career_advice: failed for %s: %s", job_id, exc)
            errors = append_error({"errors": errors}, "career_advice", "advisor_failed",
                                  str(exc), recoverable=True)
            return {
                "career_advice": None,
                "run_metrics": metrics,
                "errors": errors,
                "current_step": "career_advice",
                "updated_at": utcnow_iso(),
            }

        advice_dict = advice.model_dump()
        try:
            advice_repo.create_advice(str(uuid.uuid4()), workflow_id, job_id, advice_dict)
        except Exception as exc:
            logger.warning("career_advice: persist failed: %s", exc)

        return {
            "career_advice": advice_dict,
            "run_metrics": metrics,
            "errors": errors,
            "current_step": "career_advice",
            "updated_at": utcnow_iso(),
        }

    return career_advice
