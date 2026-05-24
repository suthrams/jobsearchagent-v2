"""Constraint analyzer — surfaces where execution limits clipped a workflow run.

Pure function over the persisted workflow state + agent_events. Returns a list
of dicts that the UI renders as a "Limits & Constraints" section. Each entry
flags an inferred constraint hit with a human-readable message; the UI never
needs to interpret raw numbers.
"""
from __future__ import annotations

from typing import Any

from app.workflows.limits import (
    MAX_LLM_CALLS_PER_JOB,
    MAX_LLM_CALLS_PER_RUN,
    MAX_REVIEW_ROUNDS,
    MAX_SELECTED_JOBS,
    MIN_MATCH_SCORE_DEFAULT,
    best_track_score,
    get_max_scored,
)


def analyze(state: dict, agent_events: list[dict] | None = None) -> list[dict]:
    """Return a list of constraint-hit findings for a single workflow run.

    Each finding: {kind, severity, message, hint?}
      severity ∈ {"info", "warning"}.
      "info"    = expected behaviour worth noting (cap reached as designed).
      "warning" = bound was hit AND it likely impacted result quality.
    """
    findings: list[dict] = []
    metrics = state.get("run_metrics") or {}
    errors = state.get("errors") or []
    raw_jobs = state.get("normalized_jobs") or state.get("raw_jobs") or []
    scored_jobs = state.get("scored_jobs") or []
    selected_jobs = state.get("selected_jobs") or []
    review_rounds = state.get("review_rounds") or []

    cfg = state.get("effective_config") or {}
    threshold = int((cfg.get("scoring") or {}).get("min_match_score", MIN_MATCH_SCORE_DEFAULT))
    # ADR-061: the meaningful processing cap is the scored-jobs limit.
    max_jobs_cfg = get_max_scored(state)

    # ── scored-jobs cap ──────────────────────────────────────────────────────
    if len(raw_jobs) >= max_jobs_cfg:
        findings.append({
            "kind": "max_jobs_cap",
            "severity": "info",
            "message": (
                f"{len(raw_jobs)} jobs surfaced from scrapers — scoring is capped at "
                f"scoring.max_scored={max_jobs_cfg}. Increase max_scored in settings "
                "to score more candidates."
            ),
        })

    # ── deep review eligibility ────────────────────────────────────────────────
    qualifying = [
        j for j in scored_jobs
        if j.get("status") == "scored" and best_track_score(j) >= threshold
    ]
    if scored_jobs and not qualifying:
        findings.append({
            "kind": "no_qualifying_jobs",
            "severity": "warning",
            "message": (
                f"None of the {len(scored_jobs)} scored jobs reached the min_match_score "
                f"threshold of {threshold} on any track. Deep review and interview prep were skipped. "
                "Lower the threshold or broaden search to see deep analysis."
            ),
        })
    elif len(qualifying) > MAX_SELECTED_JOBS:
        findings.append({
            "kind": "selected_jobs_cap",
            "severity": "info",
            "message": (
                f"{len(qualifying)} jobs qualified for deep review (any track ≥ {threshold}); "
                f"only the top {MAX_SELECTED_JOBS} were processed (MAX_SELECTED_JOBS limit)."
            ),
        })

    # ── LLM budget ─────────────────────────────────────────────────────────────
    llm_calls = int(metrics.get("llm_calls", 0))
    if llm_calls >= MAX_LLM_CALLS_PER_RUN:
        findings.append({
            "kind": "llm_budget_exhausted",
            "severity": "warning",
            "message": (
                f"LLM call budget exhausted at {llm_calls}/{MAX_LLM_CALLS_PER_RUN}. "
                "Some downstream agents may have been skipped. Raise MAX_LLM_CALLS_PER_RUN "
                "in app/workflows/limits.py if cost is acceptable."
            ),
        })
    elif llm_calls >= int(MAX_LLM_CALLS_PER_RUN * 0.85):
        findings.append({
            "kind": "llm_budget_near_cap",
            "severity": "info",
            "message": (
                f"LLM calls used: {llm_calls}/{MAX_LLM_CALLS_PER_RUN} "
                f"({llm_calls / MAX_LLM_CALLS_PER_RUN:.0%}). "
                "Approaching the per-run budget cap."
            ),
        })

    # ── deep review rounds ─────────────────────────────────────────────────────
    rounds_by_job: dict[str, list[int]] = {}
    for rd in review_rounds:
        jid = rd.get("job_id", "")
        rounds_by_job.setdefault(jid, []).append(rd.get("round_number", 0))

    capped_jobs = [jid for jid, rounds in rounds_by_job.items()
                   if max(rounds, default=0) >= MAX_REVIEW_ROUNDS]
    if capped_jobs:
        findings.append({
            "kind": "review_rounds_cap",
            "severity": "warning",
            "message": (
                f"Deep review hit MAX_REVIEW_ROUNDS={MAX_REVIEW_ROUNDS} on "
                f"{len(capped_jobs)} job(s) without the auditor reaching the quality "
                "threshold. Resume/job alignment may be marginal — review the audit notes."
            ),
        })

    # ── per-job LLM call cap signal (heuristic via metrics + selection size) ──
    if selected_jobs and llm_calls >= MAX_LLM_CALLS_PER_JOB * len(selected_jobs):
        findings.append({
            "kind": "per_job_llm_density",
            "severity": "info",
            "message": (
                f"~{llm_calls / max(1, len(selected_jobs)):.0f} LLM calls per selected job — "
                f"close to the MAX_LLM_CALLS_PER_JOB={MAX_LLM_CALLS_PER_JOB} ceiling per job."
            ),
        })

    # ── scraper failures + custom URL fetch errors from errors[] ─────────────
    scraper_errors = [e for e in errors if e.get("step") in ("discover_jobs", "custom_urls")]
    for err in scraper_errors:
        findings.append({
            "kind": "scraper_error",
            "severity": "warning",
            "message": f"{err.get('step', 'scraper')} → {err.get('error_type', '?')}: {err.get('message', '')}",
        })

    # ── 429 retry events from agent_events (best-effort) ──────────────────────
    if agent_events:
        retried = sum(1 for e in agent_events
                      if "retry" in (e.get("notes") or "").lower()
                      or "rate limit" in (e.get("output_summary") or "").lower())
        if retried:
            findings.append({
                "kind": "rate_limit_retries",
                "severity": "info",
                "message": f"{retried} agent calls retried after rate-limit (429) responses.",
            })

    return findings


def summary_metrics(state: dict) -> dict[str, Any]:
    """Compact pipeline counts for the Workflow Detail header."""
    metrics = state.get("run_metrics") or {}
    return {
        "jobs_discovered": len(state.get("normalized_jobs") or state.get("raw_jobs") or []),
        "jobs_scored": len(state.get("scored_jobs") or []),
        "jobs_selected": len(state.get("selected_jobs") or []),
        "review_rounds": len(state.get("review_rounds") or []),
        "llm_calls": int(metrics.get("llm_calls", 0)),
        "tokens_input": int(metrics.get("tokens_input", 0)),
        "tokens_output": int(metrics.get("tokens_output", 0)),
        "estimated_cost_usd": float(metrics.get("estimated_cost_usd", 0.0)),
    }
