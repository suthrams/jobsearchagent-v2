"""WorkflowGraphState — LangGraph TypedDict state for the workflow orchestrator.

Mirrors WorkflowState (Pydantic) but uses plain Python types only so LangGraph
can serialize/deserialize via its checkpointer without Pydantic dependency.
All fields are optional (total=False) because nodes return partial updates.

Two routing flags are added:
  user_requested_interview_prep — set by API layer before workflow starts
  user_requested_tailoring      — set by API layer before or during workflow
"""
from __future__ import annotations

from typing import TypedDict


class WorkflowGraphState(TypedDict, total=False):
    # ── Identity ──────────────────────────────────────────────────────────────
    workflow_id: str
    workflow_type: str
    status: str          # WorkflowStatus value as string
    current_step: str    # WorkflowStep value as string
    user_id: str | None

    # ── Resume ────────────────────────────────────────────────────────────────
    resume_id: str | None
    resume_profile: dict | None
    resume_version: int | None

    # ── Jobs ──────────────────────────────────────────────────────────────────
    search_criteria: dict
    custom_urls: list[str]        # user-supplied URLs to scrape alongside built-in scrapers
    raw_jobs: list[dict]
    normalized_jobs: list[dict]
    scored_jobs: list[dict]       # each dict gains a "status" field from scoring node
    selected_jobs: list[dict]

    # ── Research ──────────────────────────────────────────────────────────────
    research_context: dict | None
    skill_gaps: dict

    # ── Review loop ───────────────────────────────────────────────────────────
    review_rounds: list[dict]
    final_resume_review: dict | None

    # ── Career intelligence ───────────────────────────────────────────────────
    career_advice: dict | None
    interview_prep: dict | None
    tailored_resume: dict | None
    fidelity_review: dict | None

    # ── HITL ──────────────────────────────────────────────────────────────────
    pending_decision: dict | None
    human_decisions: list[dict]

    # ── Report ────────────────────────────────────────────────────────────────
    report: dict | None

    # ── Execution tracking ────────────────────────────────────────────────────
    run_metrics: dict          # flat dict matching RunMetrics fields
    errors: list[dict]         # each dict matching WorkflowError fields
    effective_config: dict

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: str
    updated_at: str

    # ── Routing flags (set by API layer, never by nodes) ─────────────────────
    user_requested_interview_prep: bool
    user_requested_tailoring: bool
