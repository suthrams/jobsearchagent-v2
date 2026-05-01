"""API response schemas — Pydantic v2 models for all HTTP response bodies."""
from __future__ import annotations

from pydantic import BaseModel, Field


class WorkflowStatusResponse(BaseModel):
    workflow_id: str
    status: str  # running | waiting_for_user | completed | failed
    current_step: str | None = None
    pending_decision: dict | None = None
    run_metrics: dict | None = None
    errors: list[dict] = []
    updated_at: str | None = None


class JobSummaryResponse(BaseModel):
    job_id: str
    title: str
    company: str
    status: str
    overall_score: int | None = None
    technical_score: int | None = None
    architecture_score: int | None = None
    leadership_score: int | None = None
    domain_score: int | None = None
    strengths: list[str] = []
    gaps: list[str] = []
    recommended_next_action: str | None = None


class ReportResponse(BaseModel):
    workflow_id: str
    report: dict  # {markdown: str, generated_at: str}
