"""score_jobs node — runs ResearchAgent + ScoringAgent for every normalised job.

Phase 8: jobs are scored concurrently (_SCORE_WORKERS parallel threads).
Each worker is independent — no shared mutable state. ScoreRepository.create()
opens its own SQLite connection per call (via get_connection) so concurrent
writes are safe.

LLM call counting mirrors the sequential version: only *successful* calls
increment the budget counter (failed calls cost 0).
"""
from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from app.agents.research_agent import ResearchAgent
from app.agents.scoring_agent import ScoringAgent
from app.providers.llm_client import LLMProviderError
from app.repositories.database import utcnow_iso
from app.repositories.score_repository import ScoreRepository
from app.services.context_trimmer import trim_resume_profile
from app.services.observability_service import ObservabilityService
from app.workflows.limits import (
    MAX_LLM_CALLS_PER_RUN,
    add_llm_calls_bulk,
    get_active_tracks,
    get_max_scored,
    get_metrics,
    safe_agent_usage_typed,
)

logger = logging.getLogger(__name__)

_SCORE_WORKERS = 5


def make_score_jobs_node(
    research_agent: ResearchAgent,
    scoring_agent: ScoringAgent,
    score_repo: ScoreRepository,
    observability: ObservabilityService,
) -> Callable[[dict], dict]:
    def score_jobs(state: dict) -> dict:
        workflow_id: str = state.get("workflow_id", "")
        resume_id: str = state.get("resume_id") or ""
        resume_profile: dict = state.get("resume_profile") or {}
        normalized_jobs: list[dict] = state.get("normalized_jobs") or []
        effective_config: dict = state.get("effective_config") or {}
        career_track: str = effective_config.get("scoring", {}).get("career_track", "all")
        # ADR-071: the profile's active track subset. The agent scores ONLY these
        # and emits null for the rest; default is all three (Primary unchanged).
        active_tracks: list[str] = get_active_tracks(state)

        metrics = get_metrics(state)
        errors = list(state.get("errors") or [])

        # ADR-061: cap at the configurable scored-jobs limit first. In auto mode
        # discovery already capped to this number, so this is a no-op there; in
        # manual mode the phase-2 endpoint also caps, so this is defense in depth.
        normalized_jobs = normalized_jobs[: get_max_scored(state)]

        # Pre-flight budget check: cap jobs to what the remaining call budget allows.
        # Each job costs at most 2 successful LLM calls (research + scoring).
        calls_used = metrics.get("llm_calls", 0)
        max_scoreable = (MAX_LLM_CALLS_PER_RUN - calls_used) // 2
        jobs_to_score = normalized_jobs[:max_scoreable]
        budget_skipped = normalized_jobs[max_scoreable:]

        if budget_skipped:
            logger.warning(
                "score_jobs: budget cap — skipping %d jobs (%d/%d calls used)",
                len(budget_skipped), calls_used, MAX_LLM_CALLS_PER_RUN,
            )

        def _score_one(job: dict) -> tuple[dict, int, list[dict], int, int, float]:
            """Research + score one job.

            Returns (entry, successful_llm_calls, errors, tokens_in, tokens_out, cost_usd).
            Token/cost values accumulate only for successful calls.
            """
            job_id = job.get("id", job.get("job_id", ""))
            entry = {**job, "job_id": job_id}
            total_ti, total_to, total_cost = 0, 0, 0.0

            # ── Research ──────────────────────────────────────────────────────
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
                logger.warning("score_jobs: research failed for %s: %s", job_id, exc)
                return {**entry, "status": "research_failed"}, 0, [{
                    "step": "scoring", "error_type": "research_failed",
                    "message": str(exc), "recoverable": True,
                    "occurred_at": utcnow_iso(), "suggested_action": None,
                }], 0, 0, 0.0

            # ── Scoring ───────────────────────────────────────────────────────
            try:
                # resume_profile is identical across every job in a run; pull it
                # into the cached system block so calls 2..N within the 5-min
                # window read it back at 10% rate instead of re-paying full
                # input price. raw_text is dropped via trim_resume_profile.
                score = scoring_agent.run(workflow_id, {
                    "_cached": {
                        "resume_profile": trim_resume_profile(resume_profile),
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
                logger.warning("score_jobs: scoring failed for %s: %s", job_id, exc)
                return {**entry, "status": "scoring_failed"}, 1, [{
                    "step": "scoring", "error_type": "scoring_failed",
                    "message": str(exc), "recoverable": True,
                    "occurred_at": utcnow_iso(), "suggested_action": None,
                }], total_ti, total_to, total_cost

            # ── Persist ───────────────────────────────────────────────────────
            try:
                score_repo.create(
                    str(uuid.uuid4()), workflow_id, job_id, resume_id, score.model_dump()
                )
            except Exception as exc:
                logger.warning("score_jobs: persist failed for %s: %s", job_id, exc)

            return {**entry, **score.model_dump(), "status": "scored"}, 2, [], total_ti, total_to, total_cost

        # ── Fan out across _SCORE_WORKERS threads ─────────────────────────────
        scored_jobs: list[dict] = []
        total_llm_calls = 0
        total_tokens_in = 0
        total_tokens_out = 0
        total_cost_usd = 0.0

        with ThreadPoolExecutor(max_workers=_SCORE_WORKERS) as executor:
            future_to_job = {
                executor.submit(_score_one, job): job for job in jobs_to_score
            }
            for future in as_completed(future_to_job):
                try:
                    entry, llm_calls, job_errors, ti, to, cost = future.result()
                except Exception as exc:
                    job = future_to_job[future]
                    job_id = job.get("id", job.get("job_id", ""))
                    logger.warning("score_jobs: worker crashed for %s: %s", job_id, exc)
                    entry = {**job, "job_id": job_id, "status": "scoring_failed"}
                    llm_calls, ti, to, cost = 0, 0, 0, 0.0
                    job_errors = [{
                        "step": "scoring", "error_type": "worker_crash",
                        "message": str(exc), "recoverable": True,
                        "occurred_at": utcnow_iso(), "suggested_action": None,
                    }]

                scored_jobs.append(entry)
                total_llm_calls += llm_calls
                total_tokens_in += ti
                total_tokens_out += to
                total_cost_usd += cost
                errors.extend(job_errors)

        # Append budget-skipped jobs after the scored ones
        for job in budget_skipped:
            job_id = job.get("id", job.get("job_id", ""))
            scored_jobs.append({**job, "job_id": job_id, "status": "budget_skipped"})

        metrics = add_llm_calls_bulk(
            metrics, total_llm_calls,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            cost_usd=total_cost_usd,
        )

        return {
            "scored_jobs": scored_jobs,
            "run_metrics": metrics,
            "errors": errors,
            "current_step": "scoring",
            "updated_at": utcnow_iso(),
        }

    return score_jobs
