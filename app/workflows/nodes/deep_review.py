"""deep_review node — reflection loop: ResumeCritic + ReviewAuditor per selected job."""
from __future__ import annotations

import logging
import uuid
from typing import Callable

from app.agents.resume_critic import ResumeCritic
from app.agents.review_auditor import ReviewAuditor
from app.providers.llm_client import LLMProviderError
from app.repositories.database import utcnow_iso
from app.repositories.review_repository import ReviewRepository
from app.services.observability_service import ObservabilityService
from app.workflows.limits import (
    AUDIT_QUALITY_THRESHOLD,
    MAX_REVIEW_ROUNDS,
    STAGNATION_MIN_IMPROVEMENT,
    add_llm_call,
    append_error,
    check_budget,
    get_metrics,
    safe_agent_usage,
    BudgetExceededError,
)

logger = logging.getLogger(__name__)


def make_deep_review_node(
    resume_critic: ResumeCritic,
    review_auditor: ReviewAuditor,
    review_repo: ReviewRepository,
    observability: ObservabilityService,
) -> Callable[[dict], dict]:
    def deep_review(state: dict) -> dict:
        workflow_id: str = state.get("workflow_id", "")
        resume_id: str = state.get("resume_id") or ""
        resume_profile: dict = state.get("resume_profile") or {}
        selected_jobs: list[dict] = state.get("selected_jobs") or []
        scored_jobs: list[dict] = state.get("scored_jobs") or []

        metrics = get_metrics(state)
        errors = list(state.get("errors") or [])
        all_rounds: list[dict] = []
        final_review: dict | None = None

        # Build a quick job_id → score dict for context passing
        score_by_job: dict[str, dict] = {}
        for sj in scored_jobs:
            jid = sj.get("job_id", sj.get("id", ""))
            if jid:
                score_by_job[jid] = sj

        for job in selected_jobs:
            job_id = job.get("job_id", job.get("id", ""))
            job_desc = job.get("job_description", "")
            job_score = score_by_job.get(job_id, {})

            round_num = 1
            prior_feedback: str | None = None
            round_scores: list[int] = []
            best_review: dict | None = None
            best_audit_score: int = -1

            while round_num <= MAX_REVIEW_ROUNDS:
                # ── Budget check ──────────────────────────────────────────
                try:
                    check_budget({"run_metrics": metrics})
                except BudgetExceededError:
                    logger.warning("deep_review: budget exhausted at round %d for %s", round_num, job_id)
                    break

                # ── ResumeCritic ──────────────────────────────────────────
                try:
                    review = resume_critic.run(workflow_id, {
                        "job_id": job_id,
                        "resume_id": resume_id,
                        "job_description": job_desc,
                        "resume_profile": resume_profile,
                        "job_score": job_score,
                        "research_context": {},
                        "prior_audit_feedback": prior_feedback,
                        "review_round": round_num,
                    })
                    _ti, _to, _cost = safe_agent_usage(resume_critic)
                    metrics = add_llm_call(metrics, tokens_in=_ti, tokens_out=_to, cost_usd=_cost)
                except LLMProviderError as exc:
                    logger.warning("deep_review: critic failed for %s round %d: %s", job_id, round_num, exc)
                    errors = append_error({"errors": errors}, "deep_review", "critic_failed",
                                         str(exc), recoverable=True)
                    # Mark job and move to next — no usable review for this job
                    job["status"] = "review_failed"
                    break

                # ── ReviewAuditor ─────────────────────────────────────────
                try:
                    check_budget({"run_metrics": metrics})
                except BudgetExceededError:
                    best_review = review.model_dump()
                    break

                try:
                    audit = review_auditor.run(workflow_id, {
                        "job_id": job_id,
                        "resume_review": review.model_dump(),
                        "resume_profile": resume_profile,
                        "job_description": job_desc,
                        "job_score": job_score,
                        "review_round": round_num,
                        "max_rounds": MAX_REVIEW_ROUNDS,
                    })
                    _ti, _to, _cost = safe_agent_usage(review_auditor)
                    metrics = add_llm_call(metrics, tokens_in=_ti, tokens_out=_to, cost_usd=_cost)
                except LLMProviderError as exc:
                    logger.warning("deep_review: auditor failed for %s round %d: %s", job_id, round_num, exc)
                    errors = append_error({"errors": errors}, "deep_review", "auditor_failed",
                                         str(exc), recoverable=True)
                    best_review = review.model_dump()
                    break

                # ── Persist round ─────────────────────────────────────────
                try:
                    review_repo.create_round(
                        str(uuid.uuid4()), workflow_id, job_id,
                        round_num, review.model_dump(), audit.model_dump(),
                        stop_reason=audit.stop_reason,
                    )
                except Exception as exc:
                    logger.warning("deep_review: persist round failed: %s", exc)

                round_entry = {
                    "round_number": round_num,
                    "job_id": job_id,
                    "critic_output": review.model_dump(),
                    "audit_output": audit.model_dump(),
                    "audit_score": audit.audit_score,
                    "stop_reason": audit.stop_reason,
                }
                all_rounds.append(round_entry)
                round_scores.append(audit.audit_score)

                if audit.audit_score > best_audit_score:
                    best_audit_score = audit.audit_score
                    best_review = review.model_dump()

                # ── Stop conditions ───────────────────────────────────────
                if audit.stop_recommendation:
                    break
                if audit.audit_score >= AUDIT_QUALITY_THRESHOLD:
                    break
                if round_num >= MAX_REVIEW_ROUNDS:
                    break
                if len(round_scores) >= 2:
                    improvement = round_scores[-1] - round_scores[-2]
                    if improvement < STAGNATION_MIN_IMPROVEMENT:
                        logger.info("deep_review: stagnation detected for %s (improvement=%d)", job_id, improvement)
                        break

                instructions = audit.recommended_revision_instructions
                prior_feedback = "\n".join(instructions) if instructions else None
                round_num += 1

            # Use last computed review if auditor never ran (critic-only round)
            if best_review is None and round_num == 1:
                pass  # no review produced — job already marked review_failed

            if best_review is not None:
                final_review = best_review
                try:
                    review_repo.create_review(
                        str(uuid.uuid4()), workflow_id, job_id,
                        resume_id, best_review,
                    )
                except Exception as exc:
                    logger.warning("deep_review: persist final review failed: %s", exc)

        return {
            "review_rounds": all_rounds,
            "final_resume_review": final_review,
            "run_metrics": metrics,
            "errors": errors,
            "current_step": "review_completed",
            "updated_at": utcnow_iso(),
        }

    return deep_review
