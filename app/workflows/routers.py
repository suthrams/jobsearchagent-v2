"""Conditional edge router functions for the LangGraph StateGraph.

Each function takes state and returns the name of the next node.
Routers are pure functions — no side effects, no DB calls.
"""
from __future__ import annotations

from app.workflows.limits import INTERVIEW_COACH_THRESHOLD


def interview_router(state: dict) -> str:
    """Route after career_advice: run InterviewCoach or skip to tailoring check."""
    scored = state.get("scored_jobs") or []
    top_score = max((j.get("overall_score", 0) for j in scored if isinstance(j, dict)), default=0)
    if top_score >= INTERVIEW_COACH_THRESHOLD or state.get("user_requested_interview_prep"):
        return "interview_prep"
    return "tailoring_check"


def tailoring_router(state: dict) -> str:
    """Route after interview_prep (or from career_advice when skipping coach): run Tailoring or go to report."""
    if state.get("user_requested_tailoring"):
        return "tailoring"
    return "generate_report"
