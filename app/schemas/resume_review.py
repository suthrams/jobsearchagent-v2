"""ResumeReview — structured output schema for the Resume Critic Agent.

The resume_only_gaps / career_gaps_observed distinction is a core invariant:
resume_only_gaps = experience exists but is poorly expressed (fixable by rewriting).
career_gaps_observed = capability is genuinely missing (not fixable by rewriting).
These must never be conflated.

Field strictness (same pattern as TailoredResumeDraft, see docstring there):
  Load-bearing fields stay required: job_id, resume_id, overall_fit_summary,
  critical_gaps, section_reviews. The reflection loop and downstream agents
  depend on these.
  Tolerant fields default to empty list / 0: resume_only_gaps,
  career_gaps_observed, suggested_improvements, questions_for_user, confidence.
  Claude (especially Haiku) sometimes omits these on simpler reviews; rejecting
  the WHOLE response over a missing list throws away the load-bearing analysis.
  An empty list is a legitimate verdict ("no gaps observed of this kind").
  The Review Auditor remains the quality safety net per ADR-008.
"""
from typing import Literal

from pydantic import BaseModel, Field


class SectionReview(BaseModel):
    section_name: str
    current_issue: str
    why_it_matters: str
    improvement_opportunity: str
    suggested_direction: str
    evidence: str
    risk_level: Literal["low", "medium", "high"]


class ResumeReview(BaseModel):
    # Load-bearing — required.
    job_id: str
    resume_id: str
    overall_fit_summary: str
    section_reviews: list[SectionReview] = Field(default_factory=list)
    critical_gaps: list[str] = Field(default_factory=list)
    # Tolerant — Haiku frequently omits these on simpler reviews. Empty list is
    # a legitimate verdict; rejecting the whole response was throwing away the
    # load-bearing analysis above.
    resume_only_gaps: list[str] = Field(default_factory=list)        # experience exists but is poorly expressed
    career_gaps_observed: list[str] = Field(default_factory=list)    # capability or proof point is genuinely missing
    suggested_improvements: list[str] = Field(default_factory=list)
    questions_for_user: list[str] = Field(default_factory=list)
    confidence: int = Field(default=0, ge=0, le=100)
