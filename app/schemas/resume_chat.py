"""Structured output schema for the ResumeChatAgent (ADR-068).

The chat agent's turn output is a short conversational `reply` plus the FULL
revised `overhaul` (reorganization + evidence-bound rewrites). The renderer
and the Fidelity Reviewer both consume the overhaul shape ADR-066 already
defined, so the chat loop reuses that schema unchanged.

`changed_sections` is for the UI and the audit trail - it doesn't gate any
runtime behaviour. The agent is told to leave non-targeted sections
unchanged in the prompt; the Fidelity Reviewer is the structural safeguard.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.resume_clinic import ResumeOverhaul

ChatSectionFocus = Literal[
    "whole",
    "headline",
    "summary",
    "experience",
    "skills",
    "education",
    "certifications",
]

ChangedSection = Literal[
    "headline",
    "summary",
    "experience",
    "skills",
    "education",
    "certifications",
    "reorganization",
]


class ResumeChatTurnResult(BaseModel):
    reply: str = Field(min_length=1, max_length=600)
    overhaul: ResumeOverhaul
    changed_sections: list[ChangedSection] = Field(default_factory=list)
