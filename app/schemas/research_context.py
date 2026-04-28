from pydantic import BaseModel, Field


class ResearchStep(BaseModel):
    step_number: int
    tool_used: str
    observation_summary: str


class ResearchContext(BaseModel):
    job_id: str
    company_summary: str
    role_context: str
    technology_signals: list[str]
    leadership_signals: list[str]
    domain_signals: list[str]
    risk_flags: list[str]
    research_steps: list[ResearchStep]
    confidence: int = Field(ge=0, le=100)
