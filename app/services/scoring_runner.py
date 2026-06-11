"""Single-job research + score + persist (ADR-100 Phase 2).

Extracted from the `score_jobs` node so the in-graph batch and the out-of-graph
on-demand endpoint (POST /workflows/{wf}/jobs/{job}/score) run identical scoring
logic — exactly how `deep_review_runner.review_one_job` was extracted from the
deep-review node. The node fans this out across a thread pool; the endpoint calls it
once for a single user-chosen (previously-unscored) job.

The research call, the cached-resume scoring call (project_resume_for_scoring keeps
the ADR-069/086 PII + cost seam), and per-job persistence are unchanged from the
original node implementation.
"""
from __future__ import annotations

import logging
import uuid

from app.providers.llm_client import LLMProviderError
from app.repositories.database import utcnow_iso
from app.services.context_trimmer import project_resume_for_scoring
from app.workflows.limits import safe_agent_usage_typed

logger = logging.getLogger(__name__)


def score_one_job(
    *,
    job: dict,
    workflow_id: str,
    resume_id: str,
    resume_profile: dict,
    career_track: str,
    active_tracks: list[str],
    research_agent,
    scoring_agent,
    score_repo,
) -> tuple[dict, int, list[dict], int, int, float]:
    """Research + score one job; persist the JobScore.

    Returns (entry, successful_llm_calls, errors, tokens_in, tokens_out, cost_usd).
    Token/cost values accumulate only for successful calls. Never raises on an LLM
    failure — returns a status entry + a recoverable error instead, so a caller
    (the node's thread pool, or the endpoint) can surface it without crashing.
    """
    job_id = job.get("id", job.get("job_id", ""))
    entry = {**job, "job_id": job_id}
    total_ti, total_to, total_cost = 0, 0, 0.0

    # ── Research ──────────────────────────────────────────────────────────────
    try:
        research = research_agent.run(workflow_id, {
            "job_id": job_id,
            "job_title": job.get("title", ""),
            "company": job.get("company", ""),
            "source_url": job.get("url", ""),
            "job_description": job.get("job_description", ""),
        })
        u = safe_agent_usage_typed(research_agent)
        total_ti += u.tokens_input; total_to += u.tokens_output; total_cost += u.cost_usd
    except LLMProviderError as exc:
        logger.warning("score_one_job: research failed for %s: %s", job_id, exc)
        return {**entry, "status": "research_failed"}, 0, [{
            "step": "scoring", "error_type": "research_failed",
            "message": str(exc), "recoverable": True,
            "occurred_at": utcnow_iso(), "suggested_action": None,
        }], 0, 0, 0.0

    # ── Scoring ───────────────────────────────────────────────────────────────
    try:
        # resume_profile is identical across every job in a run; pull it into the
        # cached system block so calls 2..N within the 5-min window read it back at
        # 10% rate. ADR-086: project_resume_for_scoring wraps trim_resume_profile
        # (raw_text dropped, PII redacted) and further drops fields the scoring
        # prompt never reads, shrinking the per-job re-sent payload.
        score = scoring_agent.run(workflow_id, {
            "_cached": {
                "resume_profile": project_resume_for_scoring(resume_profile),
            },
            "job_id": job_id,
            "resume_id": resume_id,
            "job_title": job.get("title", ""),
            "company": job.get("company", ""),
            "job_description": job.get("job_description", ""),
            "career_track": career_track,
            "active_tracks": active_tracks,
            "research_context": research.model_dump(),
        })
        u = safe_agent_usage_typed(scoring_agent)
        total_ti += u.tokens_input; total_to += u.tokens_output; total_cost += u.cost_usd
    except LLMProviderError as exc:
        logger.warning("score_one_job: scoring failed for %s: %s", job_id, exc)
        return {**entry, "status": "scoring_failed"}, 1, [{
            "step": "scoring", "error_type": "scoring_failed",
            "message": str(exc), "recoverable": True,
            "occurred_at": utcnow_iso(), "suggested_action": None,
        }], total_ti, total_to, total_cost

    # ── Persist ───────────────────────────────────────────────────────────────
    try:
        score_repo.create(
            str(uuid.uuid4()), workflow_id, job_id, resume_id, score.model_dump()
        )
    except Exception as exc:
        logger.warning("score_one_job: persist failed for %s: %s", job_id, exc)

    return {**entry, **score.model_dump(), "status": "scored"}, 2, [], total_ti, total_to, total_cost
