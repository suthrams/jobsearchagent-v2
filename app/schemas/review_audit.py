from pydantic import BaseModel, Field


class ReviewAudit(BaseModel):
    job_id: str
    round_number: int
    audit_score: int = Field(ge=0, le=100)
    auditor_confidence: int = Field(ge=0, le=100)
    quality_summary: str
    missing_analysis_points: list[str]
    generic_or_weak_feedback: list[str]
    unsupported_claims: list[str]
    fidelity_concerns: list[str]
    recommended_revision_instructions: list[str]
    stop_recommendation: bool
    stop_reason: str | None = None
