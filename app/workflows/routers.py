"""Conditional edge router functions for the LangGraph StateGraph.

Each function takes state and returns the name of the next node.
Routers are pure functions — no side effects, no DB calls.
"""
from __future__ import annotations

from app.workflows.limits import (
    get_auto_interview_prep,
    active_track_keys,
    best_track_score,
    get_manual_selection,
    get_min_match_score,
    get_relevance_filter,
)


def entry_router(state: dict) -> str:
    """Conditional entry point (ADR-060).

    A normal kickoff starts at register_run. The phase-2 scoring trigger
    re-invokes the same graph with phase="scoring" and the selected job subset;
    it must re-enter at score_jobs, not re-run discovery. Keying on `phase`
    keeps both phases inside one compiled graph and one workflow_id.
    """
    return "score_jobs" if state.get("phase") == "scoring" else "register_run"


def scoring_mode_gate(state: dict) -> str:
    """Route off load_resume between three modes (ADR-060 + ADR-079).

    Fixed precedence:
      1. manual_selection on  -> await_scoring_selection (a human triages; the LLM
         filter is pointless when a person is already curating).
      2. relevance_filter on  -> relevance_filter (a cheap LLM drops mismatches,
         then scoring continues automatically).
      3. otherwise            -> score_jobs (the unchanged default).
    """
    if get_manual_selection(state):
        return "await_scoring_selection"
    if get_relevance_filter(state):
        return "relevance_filter"
    return "score_jobs"


def deep_review_gate(state: dict) -> str:
    """After auto-select: skip deep review entirely if no jobs qualified.

    With the HITL pause removed, an empty selected_jobs list means none of the
    scored jobs hit the min_match_score on any track. We jump straight to the
    report instead of spending LLM calls on empty critic / advisor / coach runs.
    """
    selected = state.get("selected_jobs") or []
    return "deep_review" if selected else "generate_report"


def interview_router(state: dict) -> str:
    """Route after career_advice: run InterviewCoach if any selected job qualifies, else finish.

    On-demand by default (ADR-085): the in-graph coach runs only when the user
    requested it, or when a profile has opted into auto interview prep
    (scoring.auto_interview_prep) and a selected job clears min_match_score.
    """
    if state.get("user_requested_interview_prep"):
        return "interview_prep"
    if not get_auto_interview_prep(state):
        return "generate_report"
    threshold = get_min_match_score(state)
    active_keys = active_track_keys(state)
    selected = state.get("selected_jobs") or []
    top_track = max(
        (best_track_score(j, active_keys) for j in selected if isinstance(j, dict)),
        default=0,
    )
    return "interview_prep" if top_track >= threshold else "generate_report"
