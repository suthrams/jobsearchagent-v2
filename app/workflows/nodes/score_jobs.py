"""score_jobs node — runs ResearchAgent + ScoringAgent for every normalised job."""
from __future__ import annotations

import logging
import uuid
from typing import Callable

from app.agents.research_agent import ResearchAgent
from app.agents.scoring_agent import ScoringAgent
from app.providers.llm_client import LLMProviderError
from app.repositories.database import utcnow_iso
from app.repositories.score_repository import ScoreRepository
from app.services.observability_service import ObservabilityService
from app.workflows.limits import (
    BudgetExceededError,
    add_llm_call,
    append_error,
    check_budget,
    get_metrics,
)

logger = logging.getLogger(__name__)


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
        career_track: str = effective_config.get("scoring", {}).get("career_track", "ic")

        metrics = get_metrics(state)
        errors = list(state.get("errors") or [])
        scored_jobs: list[dict] = []

        for job in normalized_jobs:
            job_id = job.get("id", job.get("job_id", ""))
            job_entry = {**job, "job_id": job_id}

            # ── Budget check ──────────────────────────────────────────────────
            try:
                check_budget({"run_metrics": metrics})
            except BudgetExceededError:
                logger.warning("score_jobs: budget exhausted; marking remaining as budget_skipped")
                job_entry["status"] = "budget_skipped"
                scored_jobs.append(job_entry)
                for remaining in normalized_jobs[normalized_jobs.index(job) + 1:]:
                    rid = remaining.get("id", remaining.get("job_id", ""))
                    scored_jobs.append({**remaining, "job_id": rid, "status": "budget_skipped"})
                break

            # ── Research ──────────────────────────────────────────────────────
            research = None
            try:
                research = research_agent.run(workflow_id, {
                    "job_id": job_id,
                    "job_title": job.get("title", ""),
                    "company": job.get("company", ""),
                    "source_url": job.get("url", ""),
                    "job_description": job.get("job_description", ""),
                })
                metrics = add_llm_call(metrics)
            except LLMProviderError as exc:
                logger.warning("score_jobs: research failed for %s: %s", job_id, exc)
                errors = append_error({"errors": errors}, "scoring", "research_failed", str(exc),
                                      recoverable=True)
                job_entry["status"] = "research_failed"
                scored_jobs.append(job_entry)
                continue

            # ── Scoring ───────────────────────────────────────────────────────
            try:
                check_budget({"run_metrics": metrics})
            except BudgetExceededError:
                job_entry["status"] = "budget_skipped"
                scored_jobs.append(job_entry)
                for remaining in normalized_jobs[normalized_jobs.index(job) + 1:]:
                    rid = remaining.get("id", remaining.get("job_id", ""))
                    scored_jobs.append({**remaining, "job_id": rid, "status": "budget_skipped"})
                break

            try:
                score = scoring_agent.run(workflow_id, {
                    "job_id": job_id,
                    "resume_id": resume_id,
                    "job_title": job.get("title", ""),
                    "company": job.get("company", ""),
                    "job_description": job.get("job_description", ""),
                    "resume_profile": resume_profile,
                    "career_track": career_track,
                    "research_context": research.model_dump(),
                })
                metrics = add_llm_call(metrics)
            except LLMProviderError as exc:
                logger.warning("score_jobs: scoring failed for %s: %s", job_id, exc)
                errors = append_error({"errors": errors}, "scoring", "scoring_failed", str(exc),
                                      recoverable=True)
                job_entry["status"] = "scoring_failed"
                scored_jobs.append(job_entry)
                continue

            # ── Persist and add to state ──────────────────────────────────────
            score_dict = score.model_dump()
            try:
                score_repo.create(str(uuid.uuid4()), workflow_id, job_id, resume_id, score_dict)
            except Exception as exc:
                logger.warning("score_jobs: persist failed for %s: %s", job_id, exc)

            job_entry = {**job_entry, **score_dict, "status": "scored"}
            scored_jobs.append(job_entry)

        return {
            "scored_jobs": scored_jobs,
            "run_metrics": metrics,
            "errors": errors,
            "current_step": "scoring",
            "updated_at": utcnow_iso(),
        }

    return score_jobs
