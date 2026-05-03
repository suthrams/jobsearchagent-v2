"""Tests for the constraint analyzer that surfaces limit-hit findings."""
from __future__ import annotations

from app.services.constraint_analyzer import analyze, summary_metrics
from app.workflows.limits import (
    MAX_LLM_CALLS_PER_RUN,
    MAX_REVIEW_ROUNDS,
    MAX_SELECTED_JOBS,
)


def _scored(track_score: int, *, status: str = "scored") -> dict:
    return {
        "status": status,
        "technical_score": 0,
        "architecture_score": track_score,
        "leadership_score": 0,
    }


def test_no_qualifying_jobs_warning():
    state = {
        "scored_jobs": [_scored(50), _scored(60)],
        "effective_config": {"scoring": {"min_match_score": 75}},
    }
    findings = analyze(state)
    kinds = [f["kind"] for f in findings]
    assert "no_qualifying_jobs" in kinds


def test_selected_jobs_cap_info_when_more_qualify_than_max():
    qualifying = [_scored(80) for _ in range(MAX_SELECTED_JOBS + 2)]
    state = {
        "scored_jobs": qualifying,
        "effective_config": {"scoring": {"min_match_score": 75}},
    }
    kinds = [f["kind"] for f in analyze(state)]
    assert "selected_jobs_cap" in kinds
    assert "no_qualifying_jobs" not in kinds


def test_llm_budget_exhausted_warning():
    state = {
        "scored_jobs": [_scored(80)],
        "run_metrics": {"llm_calls": MAX_LLM_CALLS_PER_RUN},
        "effective_config": {"scoring": {"min_match_score": 75}},
    }
    kinds = [f["kind"] for f in analyze(state)]
    assert "llm_budget_exhausted" in kinds


def test_review_rounds_cap_warning():
    state = {
        "scored_jobs": [_scored(80)],
        "review_rounds": [
            {"job_id": "j1", "round_number": r} for r in range(1, MAX_REVIEW_ROUNDS + 1)
        ],
        "effective_config": {"scoring": {"min_match_score": 75}},
    }
    kinds = [f["kind"] for f in analyze(state)]
    assert "review_rounds_cap" in kinds


def test_summary_metrics_basic():
    state = {
        "normalized_jobs": [{"id": "1"}, {"id": "2"}],
        "scored_jobs": [{"id": "1"}],
        "selected_jobs": [{"id": "1"}],
        "review_rounds": [{}],
        "run_metrics": {"llm_calls": 5, "tokens_input": 100, "tokens_output": 50, "estimated_cost_usd": 0.01},
    }
    s = summary_metrics(state)
    assert s["jobs_discovered"] == 2
    assert s["jobs_scored"] == 1
    assert s["llm_calls"] == 5
