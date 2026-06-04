"""relevance_filter node — cheap reasoning pre-filter before scoring (ADR-079).

Opt-in per profile (search.relevance_filter). Runs on the auto-scoring branch
between load_resume and score_jobs. One batched LLM call (Haiku) reasons over every
discovered posting and hard-drops the ones that are a clear seniority or relevance
mismatch for the profile, so the run only pays the 2 LLM calls/job that scoring
costs on the jobs worth scoring.

Reliability contract (never lose a run to a filter fault):
  - agent call fails / unparseable -> KEEP ALL jobs, log to errors[], continue.
  - empty verdicts                 -> no job has a verdict -> KEEP ALL.
  - verdict for an unknown job_id  -> ignored (cannot drop an invented job).
  - job with no verdict            -> KEPT (absence is not a drop signal).

A legitimate "everything discovered is a mismatch" result IS allowed to drop to
zero — that is the feature working (the report then shows nothing qualified), not a
fault. The conservative "keep when unsure" prompt bias guards against over-dropping.

The profile enters the agent context ONLY through trim_resume_profile() (ADR-069),
exactly like score_jobs, so the PII-redaction invariant holds.
"""
from __future__ import annotations

import logging
from typing import Callable

from app.agents.relevance_filter_agent import RelevanceFilterAgent
from app.repositories.database import utcnow_iso
from app.services.context_trimmer import trim_resume_profile
from app.services.observability_service import ObservabilityService
from app.workflows.limits import (
    add_llm_calls_bulk,
    append_error,
    get_metrics,
    safe_agent_usage_typed,
)

logger = logging.getLogger(__name__)

# Truncate each description so one batched call stays token-bounded even at the
# wide discovery net (MAX_DISCOVERED_JOBS). The opening of a JD carries the
# seniority + role signal; the filter does not need the full benefits boilerplate.
_DESC_TRUNCATE_CHARS = 1200


def make_relevance_filter_node(
    relevance_agent: RelevanceFilterAgent,
    observability: ObservabilityService,
) -> Callable[[dict], dict]:
    def relevance_filter(state: dict) -> dict:
        workflow_id: str = state.get("workflow_id", "")
        normalized_jobs: list[dict] = state.get("normalized_jobs") or []
        resume_profile: dict = state.get("resume_profile") or {}
        search_criteria: dict = state.get("search_criteria") or {}
        search_cfg: dict = (state.get("effective_config") or {}).get("search") or {}
        discovery_stats: dict = dict(state.get("discovery_stats") or {})
        errors = list(state.get("errors") or [])
        metrics = get_metrics(state)

        if not normalized_jobs:
            return {"current_step": "relevance_filter", "updated_at": utcnow_iso()}

        target_roles = list(
            search_criteria.get("roles") or search_criteria.get("titles") or []
        )
        jobs_ctx = [
            {
                "job_id": j.get("id", j.get("job_id", "")),
                "title": j.get("title", ""),
                "company": j.get("company", ""),
                "description": (j.get("job_description") or "")[:_DESC_TRUNCATE_CHARS],
            }
            for j in normalized_jobs
        ]
        context = {
            # Redacted profile in the cached block (ADR-069 seam — keeps the PII
            # invariant; "resume_profile": + trim_resume_profile( on one line).
            "_cached": {"resume_profile": trim_resume_profile(resume_profile)},
            "target_roles": target_roles,
            "seniority_signals": {
                "min_years_experience": search_cfg.get("min_years_experience"),
                "max_years_experience": search_cfg.get("max_years_experience"),
                "exclude_senior": bool(search_cfg.get("exclude_senior", False)),
            },
            "jobs": jobs_ctx,
        }

        try:
            result = relevance_agent.run(workflow_id, context)
            usage = safe_agent_usage_typed(relevance_agent)
        except Exception as exc:
            # Never lose the run to a filter fault — keep everything, score as before.
            logger.warning(
                "relevance_filter: filter failed for %s, keeping all %d jobs: %s",
                workflow_id, len(normalized_jobs), exc,
            )
            errors = append_error(
                state, "relevance_filter", "filter_failed", str(exc), recoverable=True,
                suggested_action="All discovered jobs were kept; scoring proceeds unfiltered.",
            )
            discovery_stats["relevance_filter_error"] = str(exc)[:200]
            return {
                "normalized_jobs": normalized_jobs,
                "discovery_stats": discovery_stats,
                "errors": errors,
                "current_step": "relevance_filter",
                "updated_at": utcnow_iso(),
            }

        verdicts = {v.job_id: v for v in result.verdicts}
        kept: list[dict] = []
        dropped_audit: list[dict] = []
        for job in normalized_jobs:
            jid = job.get("id", job.get("job_id", ""))
            v = verdicts.get(jid)
            if v is not None and not v.keep:
                dropped_audit.append(
                    {"job_id": jid, "mismatch": v.mismatch, "reason": v.reason[:200]}
                )
                continue
            kept.append(job)  # no verdict or keep=true -> kept (recall-biased)

        discovery_stats["relevance_kept"] = len(kept)
        discovery_stats["relevance_dropped"] = len(dropped_audit)
        discovery_stats["relevance_drops"] = dropped_audit

        metrics = add_llm_calls_bulk(
            metrics, 1,
            tokens_in=usage.tokens_input,
            tokens_out=usage.tokens_output,
            cost_usd=usage.cost_usd,
        )

        logger.info(
            "relevance_filter: kept %d of %d jobs (dropped %d) wf=%s",
            len(kept), len(normalized_jobs), len(dropped_audit), workflow_id,
        )
        return {
            "normalized_jobs": kept,
            "discovery_stats": discovery_stats,
            "run_metrics": metrics,
            "errors": errors,
            "current_step": "relevance_filter",
            "updated_at": utcnow_iso(),
        }

    return relevance_filter
