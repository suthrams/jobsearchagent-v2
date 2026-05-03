"""TailoredResumeDraft — structured output schema for the Tailoring Agent.

Every TailoredBullet must carry supporting_evidence referencing the original
resume. claim_type="gap" means the experience does not exist and must be
labelled as a gap — never rewritten as if present. The FidelityReview agent
validates this output before it is shown to the user.

The narrative-summary fields (skills_section_suggestions,
overall_tailoring_notes, fidelity_risk_summary) are tolerant — Claude may
legitimately have nothing to add for them on a given draft, and rejecting the
whole response over an empty summary loses the per-bullet content (which is
the actual product). The load-bearing fields (per-bullet evidence, claim_type,
fidelity_risk) remain required.
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
    summary_suggestions: list[TailoredBullet] = Field(default_factory=list)
    experience_bullet_suggestions: list[TailoredBullet] = Field(default_factory=list)
    skills_section_suggestions: list[str] = Field(default_factory=list)
    overall_tailoring_notes: str = ""
    fidelity_risk_summary: str = ""
