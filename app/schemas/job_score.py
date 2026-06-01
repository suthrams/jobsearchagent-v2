"""JobScore — structured output schema for the Scoring Agent.

Scores a single resume/job pair across five dimensions (0–100). The orchestrator
uses overall_score to rank jobs and decide which qualify for deep review.

ADR-071: the three track scores are optional. A profile pursues a subset of the
three tracks (scoring.tracks); the agent scores ONLY the active tracks and emits
null for the rest. A null track is treated as 0 and never qualifies a job on its
own. overall_score and domain_score remain required.
"""
from pydantic import BaseModel, Field


class JobScore(BaseModel):
    job_id: str
    resume_id: str
    overall_score: int = Field(ge=0, le=100)
    technical_score: int | None = Field(default=None, ge=0, le=100)
    architecture_score: int | None = Field(default=None, ge=0, le=100)
    leadership_score: int | None = Field(default=None, ge=0, le=100)
    domain_score: int = Field(ge=0, le=100)
    match_summary: str
    strengths: list[str]
    gaps: list[str]
    recommended_next_action: str
    confidence: int = Field(ge=0, le=100)
