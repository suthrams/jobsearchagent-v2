"""TailoredResumeDraft — structured output schema for the Tailoring Agent.

Every TailoredBullet must carry supporting_evidence referencing the original
resume. claim_type="gap" means the experience does not exist and must be
labelled as a gap — never rewritten as if present. The FidelityReview agent
validates this output before it is shown to the user.
"""
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
