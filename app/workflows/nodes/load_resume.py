"""load_resume node — loads or parses the resume profile into workflow state."""
from __future__ import annotations

import logging
from typing import Callable

from app.repositories.database import utcnow_iso
from app.services.observability_service import ObservabilityService
from app.services.resume_parser import ResumeParser

logger = logging.getLogger(__name__)


def make_load_resume_node(
    resume_parser: ResumeParser,
    observability: ObservabilityService,
) -> Callable[[dict], dict]:
    def load_resume(state: dict) -> dict:
        workflow_id: str = state.get("workflow_id", "")

        # Already loaded — return early (idempotent)
        existing_profile = state.get("resume_profile")
        if existing_profile is not None:
            return {
                "current_step": "resume_profile_loading",
                "updated_at": utcnow_iso(),
            }

        resume_id: str | None = state.get("resume_id")
        if not resume_id:
            raise ValueError("load_resume: no resume_id or resume_profile in state")

        # Treat resume_id as a file path (orchestrator sets this before invoking the graph)
        profile = resume_parser.parse_pdf(str(resume_id), file_name=str(resume_id))
        logger.info("load_resume: parsed resume %s for workflow %s", resume_id, workflow_id)

        return {
            "resume_profile": profile.model_dump(),
            "resume_version": 1,
            "current_step": "resume_profile_loading",
            "updated_at": utcnow_iso(),
        }

    return load_resume
