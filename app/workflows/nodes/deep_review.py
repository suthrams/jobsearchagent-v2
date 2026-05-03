"""deep_review node — reflection loop: ResumeCritic + ReviewAuditor per selected job.

ADR-054 raised MAX_SELECTED_JOBS from 3 to 10. Each selected job's critic+auditor
loop is structurally independent of the others, so we fan out across
_DEEP_REVIEW_WORKERS threads — same template ADR-049 used for score_jobs (75s -> 20s).

LLM budget is pre-flighted before the executor (mirroring score_jobs); worst-case
per job = MAX_REVIEW_ROUNDS * 2 successful calls (one critic + one auditor per round).
"""
from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from app.agents.resume_critic import ResumeCritic
from app.agents.review_auditor import ReviewAuditor
from app.providers.llm_client import LLMProviderError
from app.repositories.database import utcnow_iso
from app.repositories.review_repository import ReviewRepository
from app.services.observability_service import ObservabilityService
from app.workflows.limits import (
    AUDIT_QUALITY_THRESHOLD,
    MAX_LLM_CALLS_PER_RUN,
    MAX_REVIEW_ROUNDS,
    STAGNATION_MIN_IMPROVEMENT,
    add_llm_calls_bulk,
    append_error,
    get_metrics,
    safe_agent_usage,
)

logger = logging.getLogger(__name__)

_DEEP_REVIEW_WORKERS = 5


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

        # Build a quick job_id → score dict for context passing
        score_by_job: dict[str, dict] = {}
        for sj in scored_jobs:
            jid = sj.get("job_id", sj.get("id", ""))
            if jid:
                score_by_job[jid] = sj

        # ── Pre-flight budget cap ──────────────────────────────────────────────
        # Worst case per job is MAX_REVIEW_ROUNDS * 2 successful LLM calls
        # (critic + auditor each round). Most jobs stop earlier via stop_recommendation
        # / threshold / stagnation, so this is a conservative reservation.
        calls_used = metrics.get("llm_calls", 0)
        per_job_worst_case = MAX_REVIEW_ROUNDS * 2
        max_reviewable = max(0, (MAX_LLM_CALLS_PER_RUN - calls_used) // per_job_worst_case)
        jobs_to_review = selected_jobs[:max_reviewable]
        budget_skipped = selected_jobs[max_reviewable:]

        if budget_skipped:
            logger.warning(
                "deep_review: budget cap — skipping %d jobs (%d/%d calls used)",
                len(budget_skipped), calls_used, MAX_LLM_CALLS_PER_RUN,
            )

        # ── Worker: process one job's full reflection loop ────────────────────
        def _review_one(job: dict) -> tuple[
            str,                # job_id
            list[dict],         # rounds collected for this job
            dict | None,        # best_review (or None if critic failed first round)
            list[dict],         # errors collected for this job
            int,                # successful llm_calls
            int, int, float,    # tokens_in, tokens_out, cost_usd
        ]:
            job_id = job.get("job_id", job.get("id", ""))
            job_desc = job.get("job_description", "")
            job_score = score_by_job.get(job_id, {})

            local_rounds: list[dict] = []
            local_errors: list[dict] = []
            round_scores: list[int] = []
            best_review: dict | None = None
            best_audit_score: int = -1
            llm_calls = 0
            tokens_in = tokens_out = 0
            cost_usd = 0.0

            round_num = 1
            prior_feedback: str | None = None

            while round_num <= MAX_REVIEW_ROUNDS:
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
                    ti, to, cost = safe_agent_usage(resume_critic)
                    llm_calls += 1
                    tokens_in += ti; tokens_out += to; cost_usd += cost
                except (LLMProviderError, RuntimeError) as exc:
                    logger.warning("deep_review: critic failed for %s round %d: %s",
                                   job_id, round_num, exc)
                    local_errors.append({
                        "step": "deep_review", "error_type": "critic_failed",
                        "message": str(exc), "recoverable": True,
                        "occurred_at": utcnow_iso(), "suggested_action": None,
                    })
                    job["status"] = "review_failed"
                    break

                # ── ReviewAuditor ─────────────────────────────────────────
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
                    ti, to, cost = safe_agent_usage(review_auditor)
                    llm_calls += 1
                    tokens_in += ti; tokens_out += to; cost_usd += cost
                except (LLMProviderError, RuntimeError) as exc:
                    logger.warning("deep_review: auditor failed for %s round %d: %s",
                                   job_id, round_num, exc)
                    local_errors.append({
                        "step": "deep_review", "error_type": "auditor_failed",
                        "message": str(exc), "recoverable": True,
                        "occurred_at": utcnow_iso(), "suggested_action": None,
                    })
                    best_review = review.model_dump()
                    break

                # ── Persist round (SQLite per-call connection — thread-safe) ─
                try:
                    review_repo.create_round(
                        str(uuid.uuid4()), workflow_id, job_id,
                        round_num, review.model_dump(), audit.model_dump(),
                        stop_reason=audit.stop_reason,
                    )
                except Exception as exc:
                    logger.warning("deep_review: persist round failed: %s", exc)

                local_rounds.append({
                    "round_number": round_num,
                    "job_id": job_id,
                    "critic_output": review.model_dump(),
                    "audit_output": audit.model_dump(),
                    "audit_score": audit.audit_score,
                    "stop_reason": audit.stop_reason,
                })
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
                        logger.info("deep_review: stagnation detected for %s (improvement=%d)",
                                    job_id, improvement)
                        break

                instructions = audit.recommended_revision_instructions
                prior_feedback = "\n".join(instructions) if instructions else None
                round_num += 1

            # Persist the final (best) review for this job
            if best_review is not None:
                try:
                    review_repo.create_review(
                        str(uuid.uuid4()), workflow_id, job_id,
                        resume_id, best_review,
                    )
                except Exception as exc:
                    logger.warning("deep_review: persist final review failed: %s", exc)

            return (job_id, local_rounds, best_review, local_errors,
                    llm_calls, tokens_in, tokens_out, cost_usd)

        # ── Fan out across _DEEP_REVIEW_WORKERS threads ───────────────────────
        all_rounds: list[dict] = []
        best_review_by_job: dict[str, dict] = {}
        total_llm_calls = 0
        total_tokens_in = 0
        total_tokens_out = 0
        total_cost_usd = 0.0

        with ThreadPoolExecutor(max_workers=_DEEP_REVIEW_WORKERS) as executor:
            future_to_job = {executor.submit(_review_one, job): job for job in jobs_to_review}
            for future in as_completed(future_to_job):
                try:
                    (job_id, rounds, best_review, job_errors,
                     llm_calls, ti, to, cost) = future.result()
                except Exception as exc:
                    job = future_to_job[future]
                    job_id = job.get("job_id", job.get("id", ""))
                    logger.warning("deep_review: worker crashed for %s: %s", job_id, exc)
                    errors = append_error(
                        {"errors": errors}, "deep_review", "worker_crash",
                        str(exc), recoverable=True,
                    )
                    continue

                all_rounds.extend(rounds)
                if best_review is not None:
                    best_review_by_job[job_id] = best_review
                errors.extend(job_errors)
                total_llm_calls += llm_calls
                total_tokens_in += ti
                total_tokens_out += to
                total_cost_usd += cost

        # Pick final_review deterministically: walk selected_jobs in input order
        # and choose the LAST job that has a best_review. This preserves the
        # "last writer wins" semantics of the previous sequential implementation.
        final_review: dict | None = None
        for job in jobs_to_review:
            jid = job.get("job_id", job.get("id", ""))
            if jid in best_review_by_job:
                final_review = best_review_by_job[jid]

        metrics = add_llm_calls_bulk(
            metrics, total_llm_calls,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            cost_usd=total_cost_usd,
        )

        return {
            "review_rounds": all_rounds,
            "final_resume_review": final_review,
            "run_metrics": metrics,
            "errors": errors,
            "current_step": "review_completed",
            "updated_at": utcnow_iso(),
        }

    return deep_review
