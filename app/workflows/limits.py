"""Execution limit constants and enforcement helpers for the workflow orchestrator.

All budget checks must go through check_budget() before calling any agent.
Never inline the limit logic inside nodes.
"""
from __future__ import annotations

from app.repositories.database import utcnow_iso

# ── Execution limits ──────────────────────────────────────────────────────────

MAX_JOBS_PER_RUN = 20
MAX_SELECTED_JOBS = 3
MAX_RESEARCH_STEPS = 2
MAX_REVIEW_ROUNDS = 3
MAX_LLM_CALLS_PER_JOB = 10
MAX_LLM_CALLS_PER_RUN = 50

# ── Quality thresholds ────────────────────────────────────────────────────────

AUDIT_QUALITY_THRESHOLD = 75
STAGNATION_MIN_IMPROVEMENT = 5
INTERVIEW_COACH_THRESHOLD = 75


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
