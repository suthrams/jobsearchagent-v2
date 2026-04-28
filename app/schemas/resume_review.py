"""ResumeReview — structured output schema for the Resume Critic Agent.

The resume_only_gaps / career_gaps_observed distinction is a core invariant:
resume_only_gaps = experience exists but is poorly expressed (fixable by rewriting).
career_gaps_observed = capability is genuinely missing (not fixable by rewriting).
These must never be conflated.
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
    job_id: str
    resume_id: str
    overall_fit_summary: str
    section_reviews: list[SectionReview]
    critical_gaps: list[str]
    resume_only_gaps: list[str]    # experience exists but is poorly expressed
    career_gaps_observed: list[str]  # capability or proof point is genuinely missing
    suggested_improvements: list[str]
    questions_for_user: list[str]
    confidence: int = Field(ge=0, le=100)
