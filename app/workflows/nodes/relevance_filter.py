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

        # ADR-094: optional security-clearance drop, folded into the relevance filter
        # (active only when this node runs AND the profile set search.exclude_clearance).
        # DETERMINISTIC + done BEFORE the LLM call, so clearance-gated roles cost zero
        # tokens and are dropped reliably regardless of what the agent says. Default
        # off, so a profile that WANTS cleared roles keeps them. Never-lose-the-run: a
        # detection fault keeps the job (it falls through to the LLM).
        exclude_clearance = bool(search_cfg.get("exclude_clearance", False))
        clearance_drops: list[dict] = []
        candidates: list[dict] = normalized_jobs
        if exclude_clearance:
            from app.services.clearance_filter import requires_clearance
            candidates = []
            for j in normalized_jobs:
                jid = j.get("id", j.get("job_id", ""))
                try:
                    needs = requires_clearance(j.get("job_description") or "",
                                               j.get("title") or "")
                except Exception:  # noqa: BLE001 — never lose a job to a detection fault
                    needs = False
                if needs:
                    clearance_drops.append(
                        {"job_id": jid, "mismatch": "requires_clearance",
                         "reason": "Posting requires a security clearance.",
                         "title": j.get("title") or "", "company": j.get("company") or ""})
                else:
                    candidates.append(j)
            if clearance_drops:
                logger.info("relevance_filter: clearance filter dropped %d of %d jobs wf=%s",
                            len(clearance_drops), len(normalized_jobs), workflow_id)

        # Clearance removed everything -> nothing left to send to the LLM.
        if not candidates:
            discovery_stats["relevance_kept"] = 0
            discovery_stats["relevance_dropped"] = len(clearance_drops)
            discovery_stats["relevance_drops"] = clearance_drops
            discovery_stats["clearance_dropped"] = len(clearance_drops)
            return {
                "normalized_jobs": [],
                "discovery_stats": discovery_stats,
                "errors": errors,
                "current_step": "relevance_filter",
                "updated_at": utcnow_iso(),
            }

        jobs_ctx = [
            {
                "job_id": j.get("id", j.get("job_id", "")),
                "title": j.get("title", ""),
                "company": j.get("company", ""),
                "description": (j.get("job_description") or "")[:_DESC_TRUNCATE_CHARS],
            }
            for j in candidates
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
            # Never lose the run to a filter fault — keep the candidates (clearance
            # drops are deterministic + intended, so they stay dropped even here).
            logger.warning(
                "relevance_filter: filter failed for %s, keeping %d jobs: %s",
                workflow_id, len(candidates), exc,
            )
            errors = append_error(
                state, "relevance_filter", "filter_failed", str(exc), recoverable=True,
                suggested_action="Discovered jobs were kept; scoring proceeds unfiltered.",
            )
            discovery_stats["relevance_filter_error"] = str(exc)[:200]
            discovery_stats["clearance_dropped"] = len(clearance_drops)
            if clearance_drops:
                discovery_stats["relevance_drops"] = clearance_drops
            return {
                "normalized_jobs": candidates,
                "discovery_stats": discovery_stats,
                "errors": errors,
                "current_step": "relevance_filter",
                "updated_at": utcnow_iso(),
            }

        verdicts = {v.job_id: v for v in result.verdicts}
        kept: list[dict] = []
        dropped_audit: list[dict] = list(clearance_drops)  # clearance first, then LLM
        for job in candidates:
            jid = job.get("id", job.get("job_id", ""))
            v = verdicts.get(jid)
            if v is not None and not v.keep:
                # Carry title + company so the "why filtered out" UI panel is
                # self-contained in state and needs no extra read (the dropped jobs
                # are no longer in normalized_jobs to look up).
                dropped_audit.append(
                    {"job_id": jid, "mismatch": v.mismatch, "reason": v.reason[:200],
                     "title": job.get("title") or "", "company": job.get("company") or ""}
                )
                continue
            kept.append(job)  # no verdict or keep=true -> kept (recall-biased)

        discovery_stats["relevance_kept"] = len(kept)
        discovery_stats["relevance_dropped"] = len(dropped_audit)
        discovery_stats["relevance_drops"] = dropped_audit
        discovery_stats["clearance_dropped"] = len(clearance_drops)

        metrics = add_llm_calls_bulk(
            metrics, 1,
            tokens_in=usage.tokens_input,
            tokens_out=usage.tokens_output,
            cost_usd=usage.cost_usd,
        )

        logger.info(
            "relevance_filter: kept %d of %d jobs (dropped %d, of which clearance=%d) wf=%s",
            len(kept), len(normalized_jobs), len(dropped_audit), len(clearance_drops),
            workflow_id,
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
