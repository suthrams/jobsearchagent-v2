"""Single-job deep-review reflection loop (ResumeCritic + ReviewAuditor).

Extracted from the `deep_review` node (ADR-061) so the in-graph node and the
out-of-graph on-demand endpoint (POST /workflows/{wf}/jobs/{job}/deep-review)
run identical reflection logic. The node fans this out across a thread pool;
the endpoint calls it once for a single job.

The reflection loop, its stop conditions (stop_recommendation, audit-score
threshold, max-rounds, stagnation), per-round persistence, and best-review
selection are unchanged from the original node implementation.
"""
from __future__ import annotations

import logging
import uuid

from app.providers.llm_client import LLMProviderError
from app.repositories.database import utcnow_iso
from app.services.context_trimmer import trim_resume_profile
from app.workflows.limits import (
    AUDIT_QUALITY_THRESHOLD,
    MAX_REVIEW_ROUNDS,
    STAGNATION_MIN_IMPROVEMENT,
    safe_agent_usage_typed,
)

logger = logging.getLogger(__name__)


def review_one_job(
    *,
    job: dict,
    workflow_id: str,
    resume_id: str,
    resume_profile: dict,
    job_score: dict,
    resume_critic,
    review_auditor,
    review_repo,
) -> tuple[str, list[dict], dict | None, list[dict], int, int, int, float]:
    """Run the full critic->auditor reflection loop for one job.

    Returns (job_id, rounds, best_review, errors, llm_calls, tokens_in,
    tokens_out, cost_usd). Persists each round and the final best review via
    review_repo. Token/cost values accumulate only for successful calls.
    """
    job_id = job.get("job_id", job.get("id", ""))
    job_desc = job.get("job_description", "")

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

    # resume_profile is constant across every critic+auditor call in the loop
    # (and across every job in a run). Compute the trimmed cache payload once
    # so both agents send byte-identical content - which is what Anthropic's
    # ephemeral cache needs to hit (any drift, even whitespace, misses).
    _cached_profile = {"resume_profile": trim_resume_profile(resume_profile)}

    while round_num <= MAX_REVIEW_ROUNDS:
        # ── ResumeCritic ──────────────────────────────────────────────────
        try:
            review = resume_critic.run(workflow_id, {
                "_cached": _cached_profile,
                "job_id": job_id,
                "resume_id": resume_id,
                "job_description": job_desc,
                "job_score": job_score,
                "research_context": {},
                "prior_audit_feedback": prior_feedback,
                "review_round": round_num,
            })
            u = safe_agent_usage_typed(resume_critic)
            llm_calls += 1
            tokens_in += u.tokens_input; tokens_out += u.tokens_output; cost_usd += u.cost_usd
        except (LLMProviderError, RuntimeError) as exc:
            logger.warning("review_one_job: critic failed for %s round %d: %s",
                           job_id, round_num, exc)
            local_errors.append({
                "step": "deep_review", "error_type": "critic_failed",
                "message": str(exc), "recoverable": True,
                "occurred_at": utcnow_iso(), "suggested_action": None,
            })
            job["status"] = "review_failed"
            break

        # ── ReviewAuditor ─────────────────────────────────────────────────
        try:
            audit = review_auditor.run(workflow_id, {
                "_cached": _cached_profile,
                "job_id": job_id,
                "resume_review": review.model_dump(),
                "job_description": job_desc,
                "job_score": job_score,
                "review_round": round_num,
                "max_rounds": MAX_REVIEW_ROUNDS,
            })
            u = safe_agent_usage_typed(review_auditor)
            llm_calls += 1
            tokens_in += u.tokens_input; tokens_out += u.tokens_output; cost_usd += u.cost_usd
        except (LLMProviderError, RuntimeError) as exc:
            logger.warning("review_one_job: auditor failed for %s round %d: %s",
                           job_id, round_num, exc)
            local_errors.append({
                "step": "deep_review", "error_type": "auditor_failed",
                "message": str(exc), "recoverable": True,
                "occurred_at": utcnow_iso(), "suggested_action": None,
            })
            best_review = review.model_dump()
            break

        # ── Persist round (SQLite per-call connection — thread-safe) ───────
        try:
            review_repo.create_round(
                str(uuid.uuid4()), workflow_id, job_id,
                round_num, review.model_dump(), audit.model_dump(),
                stop_reason=audit.stop_reason,
            )
        except Exception as exc:
            logger.warning("review_one_job: persist round failed: %s", exc)
            local_errors.append({  # fix 1: surface the lost round, don't swallow it
                "step": "deep_review", "error_type": "persist_failed",
                "message": f"review round {round_num} for {job_id} computed but NOT saved: {exc}",
                "recoverable": True, "occurred_at": utcnow_iso(),
                "suggested_action": "retry deep review",
            })

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

        # ── Stop conditions ────────────────────────────────────────────────
        if audit.stop_recommendation:
            break
        if audit.audit_score >= AUDIT_QUALITY_THRESHOLD:
            break
        if round_num >= MAX_REVIEW_ROUNDS:
            break
        if len(round_scores) >= 2:
            improvement = round_scores[-1] - round_scores[-2]
            if improvement < STAGNATION_MIN_IMPROVEMENT:
                logger.info("review_one_job: stagnation detected for %s (improvement=%d)",
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
            logger.warning("review_one_job: persist final review failed: %s", exc)
            local_errors.append({  # fix 1: surface the lost final review, don't swallow it
                "step": "deep_review", "error_type": "persist_failed",
                "message": f"final review for {job_id} computed but NOT saved: {exc}",
                "recoverable": True, "occurred_at": utcnow_iso(),
                "suggested_action": "retry deep review",
            })

    return (job_id, local_rounds, best_review, local_errors,
            llm_calls, tokens_in, tokens_out, cost_usd)
