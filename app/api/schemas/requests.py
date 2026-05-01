"""API request schemas — Pydantic v2 models for all incoming HTTP request bodies."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class StartWorkflowRequest(BaseModel):
    resume_id: str
    search_criteria: dict
    workflow_type: str = "full_career_review"
    effective_config: dict = Field(default_factory=dict)


class JobSelectionDecision(BaseModel):
    decision_type: Literal["select_jobs_for_deep_review"]
    selected_job_ids: list[str] = Field(min_length=1, max_length=3)


class TailoringDecision(BaseModel):
    decision_type: Literal["approve_tailoring"]
    approval: Literal["approve", "revise", "reject"]


DecisionRequest = Annotated[
    JobSelectionDecision | TailoringDecision,
    Field(discriminator="decision_type"),
]
