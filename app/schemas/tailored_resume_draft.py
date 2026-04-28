from typing import Literal

from pydantic import BaseModel, Field


class TailoredBullet(BaseModel):
    original_text: str
    suggested_text: str
    supporting_evidence: str  # must reference something in the original resume — never empty
    claim_type: Literal["reword", "emphasize", "gap"]
    fidelity_risk: Literal["low", "medium", "high"]
    unsupported_claims: list[str] = Field(default_factory=list)


class TailoredResumeDraft(BaseModel):
    job_id: str
    resume_id: str
    summary_suggestions: list[TailoredBullet]
    experience_bullet_suggestions: list[TailoredBullet]
    skills_section_suggestions: list[str]
    overall_tailoring_notes: str
    fidelity_risk_summary: str
