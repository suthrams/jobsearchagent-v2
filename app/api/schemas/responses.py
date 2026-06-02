"""API response schemas — Pydantic v2 models for all HTTP response bodies."""
from __future__ import annotations

from pydantic import BaseModel


# ── Read funnel (ADR-075) ────────────────────────────────────────────────────
# List endpoints use the uniform envelope {items, total, limit, offset} (§B.1).
# Each list gets a concrete XxxList model (Pydantic v2 + OpenAPI clarity beats a
# single generic Page[T]).


class WorkflowRunRow(BaseModel):
    """One Workflow History row (ADR-075 Phase 1). Permissive: derived/legacy runs
    leave most fields null. snake_case keys; nulls kept (not omitted) so the UI
    shape is stable."""
    workflow_id: str
    workflow_type: str | None = None
    status: str | None = None
    current_step: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None
    roles_json: str | None = None        # JSON-array text from state_json (or null)
    locations_json: str | None = None
    threshold: int | None = None
    max_jobs: int | None = None
    custom_url_count: int | None = None
    normalized_count: int | None = None
    selected_count: int | None = None
    review_rounds_count: int | None = None
    cost_usd: float | None = None
    llm_calls: int | None = None
    jobs_scored: int | None = None
    best_score: int | None = None
    avg_score: float | None = None


class WorkflowRunList(BaseModel):
    """Paged Workflow History list (the ADR-075 §B.1 envelope)."""
    items: list[WorkflowRunRow]
    total: int
    limit: int
    offset: int


class WorkflowStatusResponse(BaseModel):
    workflow_id: str
    status: str  # running | completed | failed
    current_step: str | None = None
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
    decision: str | None = None           # approve | revise | reject | edit (None until user decides)
    approved: bool = False                 # True when decision is approve or edit
    edited: dict | None = None            # human-authored final draft (present only on an edit decision)
    decided_at: str | None = None
    created_at: str | None = None


class TailoringListResponse(BaseModel):
    workflow_id: str
    tailorings: list[TailoringResponse] = []


class ResumeClinicResponse(BaseModel):
    """One Resume Clinic review row (ADR-066).

    Returned by the clinic router for both create (POST) and read (GET).
    `quality` and `overhaul` (reorganization + rewrites) are always present;
    `alignment` is null when the run had no target_role / target_track.
    """
    clinic_id: str
    user_id: str
    resume_id: str
    workflow_run_id: str | None = None
    target_role: str | None = None
    target_track: str | None = None
    seniority_aware: bool = False
    quality: dict | None = None             # ResumeQuality
    alignment: dict | None = None           # Alignment (null when no target)
    overhaul: dict | None = None            # {reorganization, rewrites}
    fidelity_review: dict | None = None     # FidelityReview verdict (null if fidelity skipped/failed)
    decision: str | None = None             # approve | revise | reject | edit
    edited: dict | None = None              # human-authored overhaul on `edit` decision
    decided_at: str | None = None
    created_at: str | None = None


class ResumeClinicListResponse(BaseModel):
    user_id: str
    reviews: list[ResumeClinicResponse] = []


class ResumeChatResponse(BaseModel):
    """ADR-068: response from one chat-revise turn on a clinic review.

    `overhaul` is the FULL revised overhaul shape ({reorganization, rewrites})
    that the renderer + Fidelity Reviewer translation glue both understand.
    `fidelity_review` is the verdict on the new rewrites; null when the agent
    emitted no rewrites or the Fidelity Reviewer LLM call failed.
    `changed_sections` is the audit trail of which sections the agent
    modified this turn (empty when nothing changed, e.g. an off-topic
    message).

    Cost tracking (added 2026-05-29):
    - `turns_used`         number of chat turns spent on this clinic so far
    - `max_turns`          cap on chat turns per clinic (MAX_CHAT_TURNS_PER_CLINIC)
    - `session_cost_usd`   cumulative cost across reviewer + chat turns +
                           fidelity, summed from the llm_calls rows tagged with
                           the clinic's workflow_run_id. Round-trippable from
                           the per-profile Cost Dashboard.
    """
    reply: str
    overhaul: dict | None
    fidelity_review: dict | None = None
    changed_sections: list[str] = []
    turns_used: int = 0
    max_turns: int = 0
    session_cost_usd: float = 0.0
