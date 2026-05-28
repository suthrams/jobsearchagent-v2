"""ResumeClinicReview — structured output schema for the Resume Reviewer agent.

ADR-066. The clinic produces three axes of output every run:

  - quality       : role-agnostic resume scorecard + findings (always present)
  - alignment     : target-role/track fit (nullable; null when no target given)
  - reorganization + rewrites : the overhaul (always present)

Rewrites mirror the tailoring suggestion shape so the existing renderer
(_render_tailored_sections / _render_one_bullet) and the Fidelity Reviewer
work unchanged. supporting_evidence is required (min_length=1) because the
overhaul is evidence-bound generation — the same invariant that holds for
job-specific tailoring (ADR-015) holds with no job present.

Quality dimensions are encoded as a Literal type so a drifted model emitting
a free-text dimension name fails schema validation rather than silently passing
through (an instance of structure-passes / meaning-shifts, the failure shape
ADR-058's pin work was shipped to catch).
"""
from typing import Literal

from pydantic import BaseModel, Field

# ── Quality scorecard ────────────────────────────────────────────────────────

QualityDimensionName = Literal[
    "structure_ordering",     # section order, resume flow
    "impact_quantification",  # numbers, scope, outcomes vs activities
    "clarity",                # readability, plain language
    "ats_formatting",         # parseable formatting, no tables/images
    "consistency",            # tense, formatting, capitalization
    "length_fit",             # page count vs experience level
    "seniority_framing",      # framing for the candidate's career stage
]

QualityRating = Literal["strong", "adequate", "needs_work"]


class QualityDimension(BaseModel):
    dimension: QualityDimensionName
    rating: QualityRating
    findings: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)


class ResumeQuality(BaseModel):
    dimensions: list[QualityDimension]
    overall_summary: str  # 2-4 sentence narrative; never empty


# ── Role/track alignment (when target given) ─────────────────────────────────

AlignmentConfidence = Literal["low", "medium", "high"]


class Alignment(BaseModel):
    fit_summary: str
    missing_skills: list[str] = Field(default_factory=list)
    missing_keywords: list[str] = Field(default_factory=list)
    suggested_certifications: list[str] = Field(default_factory=list)
    suggested_projects: list[str] = Field(default_factory=list)
    emphasize: list[str] = Field(default_factory=list)
    confidence: AlignmentConfidence


# ── Reorganization plan ──────────────────────────────────────────────────────

ReorgAction = Literal["move", "cut", "promote"]


class ReorganizationMove(BaseModel):
    action: ReorgAction
    subject: str   # which section / bullet (resume identifier)
    rationale: str # one short sentence; never empty


class Reorganization(BaseModel):
    section_order: list[str]  # the proposed top-down section order
    moves: list[ReorganizationMove] = Field(default_factory=list)


# ── Rewrites (evidence-bound, mirrors tailoring shape) ───────────────────────

RewriteClaimType = Literal["restate", "reorder", "quantify", "reframe"]


class RewriteSuggestion(BaseModel):
    section_label: str
    original_text: str
    suggested_text: str
    claim_type: RewriteClaimType
    supporting_evidence: str = Field(min_length=1)  # must reference the source resume


# ── Top-level review ─────────────────────────────────────────────────────────


class ResumeClinicReview(BaseModel):
    quality: ResumeQuality
    alignment: Alignment | None = None           # null when no target given
    reorganization: Reorganization
    rewrites: list[RewriteSuggestion] = Field(default_factory=list)
