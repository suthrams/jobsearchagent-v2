"""Execution limit constants and enforcement helpers for the workflow orchestrator.

All budget checks must go through check_budget() before calling any agent.
Never inline the limit logic inside nodes.
"""
from __future__ import annotations

from app.repositories.database import utcnow_iso

# ── Execution limits ──────────────────────────────────────────────────────────

MAX_JOBS_PER_RUN = 10
MAX_SELECTED_JOBS = 3
MAX_RESEARCH_STEPS = 2
MAX_REVIEW_ROUNDS = 3
MAX_LLM_CALLS_PER_JOB = 10
MAX_LLM_CALLS_PER_RUN = 100

# ── Quality thresholds ────────────────────────────────────────────────────────

AUDIT_QUALITY_THRESHOLD = 75
STAGNATION_MIN_IMPROVEMENT = 5

# Default minimum match score — a job qualifies for deep review / interview prep
# if ANY of {technical_score, architecture_score, leadership_score} >= this value.
# Overridable via effective_config['scoring']['min_match_score'].
MIN_MATCH_SCORE_DEFAULT = 75

# Track score keys checked by qualifies_for_deep_review().
_TRACK_SCORE_KEYS = ("technical_score", "architecture_score", "leadership_score")


class BudgetExceededError(Exception):
    """Raised when MAX_LLM_CALLS_PER_RUN is hit during a scoring or review loop."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_metrics(state: dict) -> dict:
    """Return run_metrics as a plain dict regardless of its type in state."""
    m = state.get("run_metrics", {})
    if m is None:
        return {}
    if hasattr(m, "model_dump"):
        return m.model_dump()
    return dict(m)


def check_budget(state: dict) -> None:
    """Raise BudgetExceededError if the LLM call budget is exhausted."""
    calls = get_metrics(state).get("llm_calls", 0)
    if calls >= MAX_LLM_CALLS_PER_RUN:
        raise BudgetExceededError(
            f"LLM call budget exhausted ({calls}/{MAX_LLM_CALLS_PER_RUN})"
        )


def add_llm_call(
    metrics: dict,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
) -> dict:
    """Return a new metrics dict with one LLM call incremented."""
    return {
        "llm_calls": metrics.get("llm_calls", 0) + 1,
        "tokens_input": metrics.get("tokens_input", 0) + tokens_in,
        "tokens_output": metrics.get("tokens_output", 0) + tokens_out,
        "estimated_cost_usd": metrics.get("estimated_cost_usd", 0.0) + cost_usd,
        "total_duration_ms": metrics.get("total_duration_ms", 0),
        "started_at": metrics.get("started_at"),
        "completed_at": metrics.get("completed_at"),
    }


def safe_agent_usage(agent) -> tuple[int, int, float]:
    """Read last_call_usage() from an agent with a (0, 0, 0.0) fallback.

    Guards against mock objects that return non-tuple values — test doubles never
    need to configure last_call_usage; real providers always return (int, int, float).
    """
    try:
        ti, to, cost = agent.last_call_usage()
        return int(ti), int(to), float(cost)
    except Exception:
        return 0, 0, 0.0


def add_llm_calls_bulk(
    metrics: dict,
    count: int,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
) -> dict:
    """Return a new metrics dict with multiple LLM calls added at once.

    Used by concurrent nodes (e.g. score_jobs) where calls are accumulated
    across threads before being flushed to the metrics dict.
    """
    return {
        "llm_calls": metrics.get("llm_calls", 0) + count,
        "tokens_input": metrics.get("tokens_input", 0) + tokens_in,
        "tokens_output": metrics.get("tokens_output", 0) + tokens_out,
        "estimated_cost_usd": metrics.get("estimated_cost_usd", 0.0) + cost_usd,
        "total_duration_ms": metrics.get("total_duration_ms", 0),
        "started_at": metrics.get("started_at"),
        "completed_at": metrics.get("completed_at"),
    }


def get_min_match_score(state: dict) -> int:
    """Return the per-run min match threshold from effective_config, falling back to default."""
    cfg = state.get("effective_config") or {}
    scoring = cfg.get("scoring") or {}
    raw = scoring.get("min_match_score", MIN_MATCH_SCORE_DEFAULT)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return MIN_MATCH_SCORE_DEFAULT


def best_track_score(scored_job: dict) -> int:
    """Highest of the three track scores on a scored job dict. Missing scores treated as 0."""
    best = 0
    for key in _TRACK_SCORE_KEYS:
        try:
            v = int(scored_job.get(key, 0) or 0)
        except (TypeError, ValueError):
            v = 0
        if v > best:
            best = v
    return best


def qualifies_for_deep_review(scored_job: dict, threshold: int) -> bool:
    """True if any of the three track scores meets or exceeds the threshold."""
    return best_track_score(scored_job) >= threshold


def append_error(
    state: dict,
    step: str,
    error_type: str,
    message: str,
    recoverable: bool,
    suggested_action: str | None = None,
) -> list[dict]:
    """Return a new errors list with one error appended."""
    errors = list(state.get("errors") or [])
    errors.append({
        "step": step,
        "error_type": error_type,
        "message": message,
        "recoverable": recoverable,
        "occurred_at": utcnow_iso(),
        "suggested_action": suggested_action,
    })
    return errors
