"""tailoring node — TailoringAgent + FidelityReviewer (always paired)."""
from __future__ import annotations

import logging
import uuid
from typing import Callable

from app.agents.fidelity_reviewer import FidelityReviewer
from app.agents.tailoring_agent import TailoringAgent
from app.providers.llm_client import LLMProviderError
from app.repositories.database import utcnow_iso
from app.repositories.tailoring_repository import TailoringRepository
from app.services.observability_service import ObservabilityService
from app.workflows.limits import add_llm_call, append_error, get_metrics, safe_agent_usage

logger = logging.getLogger(__name__)


def make_tailoring_node(
    tailoring_agent: TailoringAgent,
    fidelity_reviewer: FidelityReviewer,
    tailoring_repo: TailoringRepository,
    observability: ObservabilityService,
) -> Callable[[dict], dict]:
    def tailoring(state: dict) -> dict:
        workflow_id: str = state.get("workflow_id", "")
        resume_id: str = state.get("resume_id") or ""
        resume_profile: dict = state.get("resume_profile") or {}
        selected_jobs: list[dict] = state.get("selected_jobs") or []
        career_advice: dict | None = state.get("career_advice")
        final_review: dict | None = state.get("final_resume_review")

        metrics = get_metrics(state)
        errors = list(state.get("errors") or [])

        if not selected_jobs:
            return {
                "tailored_resume": None,
                "fidelity_review": None,
                "current_step": "awaiting_user_approval",
                "updated_at": utcnow_iso(),
            }

        top_job = max(selected_jobs, key=lambda j: j.get("overall_score", 0))
        job_id = top_job.get("job_id", top_job.get("id", ""))
        job_desc = top_job.get("job_description", "")

        # ── TailoringAgent ────────────────────────────────────────────────────
        try:
            draft = tailoring_agent.run(workflow_id, {
                "job_id": job_id,
                "resume_id": resume_id,
                "job_description": job_desc,
                "resume_profile": resume_profile,
                "final_review": final_review or {},
                "career_advice": career_advice or {},
            })
            _ti, _to, _cost = safe_agent_usage(tailoring_agent)
            metrics = add_llm_call(metrics, tokens_in=_ti, tokens_out=_to, cost_usd=_cost)
        except LLMProviderError as exc:
            logger.warning("tailoring: TailoringAgent failed for %s: %s", job_id, exc)
            errors = append_error({"errors": errors}, "tailoring", "tailoring_failed",
                                  str(exc), recoverable=True)
            return {
                "tailored_resume": None,
                "fidelity_review": None,
                "run_metrics": metrics,
                "errors": errors,
                "current_step": "awaiting_user_approval",
                "updated_at": utcnow_iso(),
            }

        draft_dict = draft.model_dump()

        # ── FidelityReviewer — always runs; never skipped ─────────────────────
        try:
            fidelity = fidelity_reviewer.run(workflow_id, {
                "job_id": job_id,
                "resume_id": resume_id,
                "job_description": job_desc,
                "resume_profile": resume_profile,
                "tailored_draft": draft_dict,
            })
            _ti, _to, _cost = safe_agent_usage(fidelity_reviewer)
            metrics = add_llm_call(metrics, tokens_in=_ti, tokens_out=_to, cost_usd=_cost)
        except LLMProviderError as exc:
            logger.warning("tailoring: FidelityReviewer failed for %s: %s", job_id, exc)
            errors = append_error({"errors": errors}, "tailoring", "fidelity_failed",
                                  str(exc), recoverable=True)
            return {
                "tailored_resume": draft_dict,
                "fidelity_review": None,
                "run_metrics": metrics,
                "errors": errors,
                "current_step": "awaiting_user_approval",
                "updated_at": utcnow_iso(),
            }

        fidelity_dict = fidelity.model_dump()

        try:
            tailoring_repo.create(str(uuid.uuid4()), workflow_id, job_id, draft_dict)
        except Exception as exc:
            logger.warning("tailoring: persist failed: %s", exc)

        return {
            "tailored_resume": draft_dict,
            "fidelity_review": fidelity_dict,
            "run_metrics": metrics,
            "errors": errors,
            "current_step": "awaiting_user_approval",
            "updated_at": utcnow_iso(),
        }

    return tailoring
