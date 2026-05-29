"""Execution limit constants and enforcement helpers for the workflow orchestrator.

All budget checks must go through check_budget() before calling any agent.
Never inline the limit logic inside nodes.
"""
from __future__ import annotations

from app.repositories.database import utcnow_iso

# ── Execution limits ──────────────────────────────────────────────────────────

# ADR-061: MAX_JOBS_PER_RUN is now the *default* scored-jobs cap, overridable
# per run via effective_config['scoring']['max_scored'] up to MAX_SCORED_CEILING.
# Read it through get_max_scored(state) — never inline the constant for the
# scored cap.
MAX_JOBS_PER_RUN = 10
# Hard ceiling for scoring.max_scored (ADR-061). The user owns the scored width
# within this cost-safe envelope; MAX_LLM_CALLS_PER_RUN is the absolute backstop
# (25 scored jobs = at most 50 research+scoring calls, well inside 200).
MAX_SCORED_CEILING = 25
# ADR-060: in manual-selection mode, discovery casts a wider net (this cap) and
# the user picks which jobs to score; the scored cap (get_max_scored) still
# bounds how many of the selected jobs are scored per phase-2 trigger, so the
# cost ceiling holds. ADR-061 makes the net width configurable via
# effective_config['search']['max_discovered'] up to this value, which is both
# the default and the hard ceiling. Only effective when manual_selection is true.
MAX_DISCOVERED_JOBS = 50
# Cost cut: lowered from 10 (ADR-054) to 3 to cap deep-review fan-out.
# ADR-054's design intent (every qualifying job reaches deep review) still
# holds; this only changes the budget cap for how many qualifying jobs we'll
# pay for per run. Raise per-run via effective_config if a session warrants it.
MAX_SELECTED_JOBS = 3
MAX_RESEARCH_STEPS = 2
# Cost cut: lowered from 3. The reflection loop usually converges by round 2
# (see review_rounds.stop_reason data); round 3 rarely changes the verdict.
MAX_REVIEW_ROUNDS = 2
MAX_LLM_CALLS_PER_JOB = 10
MAX_LLM_CALLS_PER_RUN = 200

# ADR-068: bounded chat-revise sessions. One chat turn = 1 ResumeChatAgent call
# + (when there are rewrites) 1 FidelityReviewer call ~= $0.017 uncached. 25
# turns caps the worst case for one clinic at ~$0.45. Override at runtime via
# the RESUME_CHAT_MAX_TURNS env var (use sparingly; bias is "block first").
# Counts the number of past `resume_chat` agent calls tagged with the clinic's
# workflow_run_id - so the cap survives across sessions / API restarts.
MAX_CHAT_TURNS_PER_CLINIC = 25

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


def safe_agent_usage_typed(agent):
    """Read last_call_usage_typed() from an agent with a zero-LLMUsage fallback.

    Guards against mock objects that haven't configured the typed accessor —
    older test doubles only set up the legacy tuple, so fall through to the
    safe default (zero usage) on any failure.
    """
    from app.providers.llm_client import LLMUsage
    try:
        usage = agent.last_call_usage_typed()
        if isinstance(usage, LLMUsage):
            return usage
        # Test doubles that override last_call_usage_typed with a non-LLMUsage value
        # land here — coerce or fall through to zero.
    except Exception:
        pass
    # Final fallback — try the legacy tuple shape, then zero.
    try:
        ti, to, cost = agent.last_call_usage()
        return LLMUsage(tokens_input=int(ti), tokens_output=int(to), cost_usd=float(cost))
    except Exception:
        return LLMUsage()


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


def get_manual_selection(state: dict) -> bool:
    """Return whether manual scoring selection is enabled for this run (ADR-060).

    When true, the graph discovers a wider net of jobs and stops for the user to
    pick which ones to score, instead of auto-scoring every discovered job.
    Read from effective_config['scoring']['manual_selection']; default False.
    """
    cfg = state.get("effective_config") or {}
    scoring = cfg.get("scoring") or {}
    return bool(scoring.get("manual_selection", False))


def get_max_scored(state: dict) -> int:
    """Per-run cap on how many jobs get scored (ADR-061).

    Read from effective_config['scoring']['max_scored']; defaults to
    MAX_JOBS_PER_RUN and is clamped to [1, MAX_SCORED_CEILING] for cost safety.
    This is the authoritative gate: per-run effective_config can arrive
    un-clamped (the UI builds it directly, bypassing ConfigService), so the
    clamp must live here as well as in ConfigService._enforce_limits.
    """
    cfg = state.get("effective_config") or {}
    scoring = cfg.get("scoring") or {}
    raw = scoring.get("max_scored", MAX_JOBS_PER_RUN)
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return MAX_JOBS_PER_RUN
    return max(1, min(val, MAX_SCORED_CEILING))


def get_max_discovered_jobs(state: dict) -> int:
    """Discovery cap for this run (ADR-060 + ADR-061).

    Manual-selection mode: the wide net from effective_config['search']
    ['max_discovered'], defaulting to and clamped at MAX_DISCOVERED_JOBS.
    Auto mode: equals the scored cap — there is no point discovering more jobs
    than we will score, since auto mode scores every discovered job.
    """
    if not get_manual_selection(state):
        return get_max_scored(state)
    cfg = state.get("effective_config") or {}
    search = cfg.get("search") or {}
    raw = search.get("max_discovered", MAX_DISCOVERED_JOBS)
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return MAX_DISCOVERED_JOBS
    return max(1, min(val, MAX_DISCOVERED_JOBS))


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
