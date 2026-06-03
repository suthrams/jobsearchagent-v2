"""deep_review node — reflection loop: ResumeCritic + ReviewAuditor per selected job.

ADR-054 raised MAX_SELECTED_JOBS from 3 to 10. Each selected job's critic+auditor
loop is structurally independent of the others, so we fan out across
_DEEP_REVIEW_WORKERS threads — same template ADR-049 used for score_jobs (75s -> 20s).

LLM budget is pre-flighted before the executor (mirroring score_jobs); worst-case
per job = MAX_REVIEW_ROUNDS * 2 successful calls (one critic + one auditor per round).
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from app.agents.resume_critic import ResumeCritic
from app.agents.review_auditor import ReviewAuditor
from app.repositories.database import utcnow_iso
from app.repositories.review_repository import ReviewRepository
from app.services.deep_review_runner import review_one_job
from app.services.observability_service import (
    ObservabilityService,
    budget_cap_security_description,
)
from app.workflows.limits import (
    MAX_LLM_CALLS_PER_RUN,
    MAX_REVIEW_ROUNDS,
    add_llm_calls_bulk,
    append_error,
    get_metrics,
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
            # ADR-076: the cost guardrail tripped — make the truncation observable
            # (never-crash; PII-safe counts only) instead of only a log line.
            observability.log_security_event(
                workflow_id=workflow_id,
                event_type="budget_cap_reached",
                severity="warning",
                description=budget_cap_security_description(
                    "deep_review", len(budget_skipped), calls_used, MAX_LLM_CALLS_PER_RUN
                ),
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
            return review_one_job(
                job=job,
                workflow_id=workflow_id,
                resume_id=resume_id,
                resume_profile=resume_profile,
                job_score=score_by_job.get(job_id, {}),
                resume_critic=resume_critic,
                review_auditor=review_auditor,
                review_repo=review_repo,
            )

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
