"""Conditional edge router functions for the LangGraph StateGraph.

Each function takes state and returns the name of the next node.
Routers are pure functions — no side effects, no DB calls.
"""
from __future__ import annotations

from app.workflows.limits import (
    best_track_score,
    get_min_match_score,
)


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

    A job qualifies if any of its track scores (technical/architecture/leadership)
    meets the per-run min_match_score, or if the user explicitly requested coaching.
    """
    threshold = get_min_match_score(state)
    selected = state.get("selected_jobs") or []
    top_track = max((best_track_score(j) for j in selected if isinstance(j, dict)), default=0)
    if top_track >= threshold or state.get("user_requested_interview_prep"):
        return "interview_prep"
    return "generate_report"
