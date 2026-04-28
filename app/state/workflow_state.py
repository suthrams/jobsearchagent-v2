"""Workflow state types — the single source of truth for a running workflow execution.

Only the orchestrator reads and writes WorkflowState. Agents receive selected
portions as input and return structured outputs; they never mutate state directly.
"""
from enum import Enum

from pydantic import BaseModel, Field


class WorkflowStatus(str, Enum):
    INITIALIZED = "initialized"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowStep(str, Enum):
    INITIALIZED = "initialized"
    JOB_DISCOVERY = "job_discovery"
    RESUME_PROFILE_LOADING = "resume_profile_loading"
    SCORING = "scoring"
    AWAITING_JOB_SELECTION = "awaiting_job_selection"
    RESEARCH = "research"
    RESUME_CRITIQUE = "resume_critique"
    REVIEW_AUDIT = "review_audit"
    REFLECTION_DECISION = "reflection_decision"
    CAREER_ADVICE = "career_advice"
    INTERVIEW_PREP = "interview_prep"
    TAILORING = "tailoring"
    FIDELITY_REVIEW = "fidelity_review"
    AWAITING_USER_APPROVAL = "awaiting_user_approval"
    REPORT_GENERATION = "report_generation"
    COMPLETED = "completed"
    FAILED = "failed"


class StepExecution(BaseModel):
    step: WorkflowStep
    status: str  # started | completed | failed | skipped
    started_at: str  # ISO 8601 UTC
    completed_at: str | None = None
    duration_ms: int | None = None
    notes: str | None = None


class RunMetrics(BaseModel):
    llm_calls: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    estimated_cost_usd: float = 0.0
    total_duration_ms: int = 0
    started_at: str | None = None   # ISO 8601 UTC — set at workflow start
    completed_at: str | None = None  # ISO 8601 UTC — set at workflow end or failure


class WorkflowError(BaseModel):
    step: str
    error_type: str
    message: str
    recoverable: bool
    occurred_at: str  # ISO 8601 UTC
    suggested_action: str | None = None


class HumanDecision(BaseModel):
    decision_type: str
    decision_value: str
    payload: dict = Field(default_factory=dict)
    presented_at: str  # ISO 8601 UTC — when the UI showed the checkpoint
    decided_at: str    # ISO 8601 UTC — when the user submitted their response


class WorkflowState(BaseModel):
    # Identity
    workflow_id: str
    workflow_type: str
    status: WorkflowStatus
    current_step: WorkflowStep

    user_id: str | None = None

    # Resume
    resume_id: str | None = None
    resume_profile: dict | None = None
    resume_version: int | None = None

    # Jobs
    search_criteria: dict = Field(default_factory=dict)
    raw_jobs: list[dict] = Field(default_factory=list)
    normalized_jobs: list[dict] = Field(default_factory=list)
    scored_jobs: list[dict] = Field(default_factory=list)
    selected_jobs: list[dict] = Field(default_factory=list)

    # Research
    research_context: dict | None = None
    skill_gaps: dict = Field(default_factory=dict)

    # Review loop
    review_rounds: list[dict] = Field(default_factory=list)
    final_resume_review: dict | None = None

    # Career intelligence
    career_advice: dict | None = None
    interview_prep: dict | None = None
    tailored_resume: dict | None = None
    fidelity_review: dict | None = None

    # HITL
    pending_decision: dict | None = None
    human_decisions: list[HumanDecision] = Field(default_factory=list)

    # Report
    report: dict | None = None

    # Execution tracking — step_history is append-only; never pruned within a run
    step_history: list[StepExecution] = Field(default_factory=list)
    run_metrics: RunMetrics = Field(default_factory=RunMetrics)
    errors: list[WorkflowError] = Field(default_factory=list)

    # Snapshot of config used for this run — injected at start, never mutated mid-run
    effective_config: dict = Field(default_factory=dict)

    # Run-level timestamps
    created_at: str   # ISO 8601 UTC — set once at creation
    updated_at: str   # ISO 8601 UTC — refreshed on every state write
