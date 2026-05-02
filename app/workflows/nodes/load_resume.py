"""load_resume node — loads or parses the resume profile into workflow state."""
from __future__ import annotations

import json
import logging
from typing import Callable

from app.repositories.database import utcnow_iso
from app.repositories.resume_repository import ResumeRepository
from app.schemas.resume_profile import ResumeProfile
from app.services.observability_service import ObservabilityService
from app.services.resume_parser import ResumeParser

logger = logging.getLogger(__name__)


def make_load_resume_node(
    resume_parser: ResumeParser,
    observability: ObservabilityService,
    resume_repo: ResumeRepository,
) -> Callable[[dict], dict]:
    def load_resume(state: dict) -> dict:
        workflow_id: str = state.get("workflow_id", "")

        # Already loaded — return early (idempotent)
        if state.get("resume_profile") is not None:
            return {
                "current_step": "resume_profile_loading",
                "updated_at": utcnow_iso(),
            }

        resume_id: str | None = state.get("resume_id")
        if not resume_id:
            raise ValueError("load_resume: no resume_id or resume_profile in state")

        # 1. Check DB first — supports pre-seeded and previously-parsed profiles
        row = resume_repo.get_by_id(resume_id)
        if row:
            profile = ResumeProfile.model_validate(
                json.loads(row["parsed_profile_json"])
            )
            logger.info("load_resume: cache hit for resume_id %s in workflow %s", resume_id, workflow_id)
            return {
                "resume_profile": profile.model_dump(),
                "resume_version": row.get("version", 1),
                "current_step": "resume_profile_loading",
                "updated_at": utcnow_iso(),
            }

        # 2. Fall back to parsing only when resume_id looks like a PDF file path.
        # A bare DB id (e.g. a UUID or fixture key) that is missing from the DB is a
        # caller error — raise early with a clear message rather than letting pdfminer
        # try to open a string like "res-phase7-001" as a file.
        from pathlib import Path as _Path
        _resume_path = _Path(str(resume_id))
        if _resume_path.suffix.lower() != ".pdf":
            raise ValueError(
                f"load_resume: resume_id '{resume_id}' not found in database. "
                "Parse the resume PDF first (e.g. via ResumeParser.parse_pdf) and "
                "pass the returned resume_id to the workflow."
            )
        profile = resume_parser.parse_pdf(str(resume_id), file_name=_resume_path.name)
        logger.info("load_resume: parsed PDF %s for workflow %s", resume_id, workflow_id)

        return {
            "resume_profile": profile.model_dump(),
            "resume_version": 1,
            "current_step": "resume_profile_loading",
            "updated_at": utcnow_iso(),
        }

    return load_resume
