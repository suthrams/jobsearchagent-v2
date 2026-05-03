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


class TailoringResponse(BaseModel):
    """One tailoring draft + its fidelity review + the user's decision (if any).

    Returned by the tailoring router for both create (POST) and read (GET) endpoints
    so the consumer always sees the same shape regardless of how it was retrieved.
    """
    tailoring_id: str
    workflow_id: str
    job_id: str
    resume_id: str
    tailored: dict | None = None          # TailoredResumeDraft fields
    fidelity_review: dict | None = None   # FidelityReview fields
    decision: str | None = None           # approve | revise | reject (None until user decides)
    approved: bool = False                 # legacy boolean; True only when decision == "approve"
    decided_at: str | None = None
    created_at: str | None = None


class TailoringListResponse(BaseModel):
    workflow_id: str
    tailorings: list[TailoringResponse] = []
