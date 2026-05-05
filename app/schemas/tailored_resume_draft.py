"""TailoredResumeDraft — structured output schema for the Tailoring Agent.

Every TailoredBullet must carry supporting_evidence referencing the original
resume. claim_type="gap" means the experience does not exist and must be
labelled as a gap — never rewritten as if present. claim_type="remove" means
the bullet does not pull weight for this specific job and the candidate should
delete it to free space for higher-value content; suggested_text is empty for
remove suggestions. The FidelityReview agent validates this output before it
is shown to the user.

section_label identifies the resume section a suggestion targets, so the UI
can group suggestions in resume order rather than as a flat list. Use the
candidate's actual section identifiers:
  - "summary"
  - "experience:<company>:<title>"   (one per ExperienceEntry)
  - "skills"
  - "education:<institution>"        (rare)
  - "certifications:<name>"          (rare)

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
    suggested_text: str  # empty for claim_type="remove" or claim_type="gap"
    supporting_evidence: str  # must reference something in the original resume — never empty
    claim_type: Literal["reword", "emphasize", "gap", "remove"]
    fidelity_risk: Literal["low", "medium", "high"]
    section_label: str = ""  # e.g. "summary", "experience:Acme:Staff Engineer", "skills"
    unsupported_claims: list[str] = Field(default_factory=list)


class TailoredResumeDraft(BaseModel):
    job_id: str
    resume_id: str
    summary_suggestions: list[TailoredBullet] = Field(default_factory=list)
    experience_bullet_suggestions: list[TailoredBullet] = Field(default_factory=list)
    skills_section_suggestions: list[str] = Field(default_factory=list)
    overall_tailoring_notes: str = ""
    fidelity_risk_summary: str = ""
