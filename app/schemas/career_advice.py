"""CareerAdvice — structured output schema for the Career Advisor Agent.

Separates resume_gaps (expression problems) from career_gaps (real development
gaps). This distinction drives whether the Tailoring Agent can act or whether
the user needs to build new experience.
"""
from pydantic import BaseModel, Field


class CareerAdvice(BaseModel):
    job_id: str
    positioning_summary: str
    resume_gaps: list[str]          # expression problems — experience exists, poorly shown
    career_gaps: list[str]          # real development gaps — capability not yet built
    role_fit_assessment: str
    recommended_positioning: str
    skills_to_strengthen: list[str]
    experience_to_collect: list[str]
    thirty_sixty_ninety_day_plan: list[str]
    recommended_next_action: str
    confidence: int = Field(ge=0, le=100)
