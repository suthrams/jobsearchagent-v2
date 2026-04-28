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
