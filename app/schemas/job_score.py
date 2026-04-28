from pydantic import BaseModel, Field


class JobScore(BaseModel):
    job_id: str
    resume_id: str
    overall_score: int = Field(ge=0, le=100)
    technical_score: int = Field(ge=0, le=100)
    architecture_score: int = Field(ge=0, le=100)
    leadership_score: int = Field(ge=0, le=100)
    domain_score: int = Field(ge=0, le=100)
    match_summary: str
    strengths: list[str]
    gaps: list[str]
    recommended_next_action: str
    confidence: int = Field(ge=0, le=100)
