"""InterviewPrep — structured output schema for the Interview Coach Agent.

Only produced when overall_score >= interview_prep_threshold or explicitly
requested by the user. Never invents stories — all content is grounded in
the resume and job description provided.
"""
from pydantic import BaseModel, Field


class InterviewPrep(BaseModel):
    job_id: str
    likely_interview_topics: list[str]
    technical_topics_to_review: list[str]
    leadership_stories_to_prepare: list[str]
    weak_areas_to_defend: list[str]
    questions_to_ask_interviewer: list[str]
    seven_day_prep_plan: list[str]
    confidence: int = Field(ge=0, le=100)
