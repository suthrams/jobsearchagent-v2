from typing import Literal

from pydantic import BaseModel, Field


class FidelityReview(BaseModel):
    job_id: str
    resume_id: str
    overall_fidelity_status: Literal["pass", "fail", "needs_revision"]
    unsupported_claims: list[str]
    fabricated_metrics: list[str]
    inflated_scope_flags: list[str]
    unsupported_technology_flags: list[str]
    unsupported_certification_flags: list[str]
    required_removals: list[str]
    required_revisions: list[str]
    approval_recommendation: Literal["approve", "revise", "reject"]
    confidence: int = Field(ge=0, le=100)
