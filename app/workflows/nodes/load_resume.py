"""load_resume node — loads or parses the resume profile into workflow state."""
from __future__ import annotations

import json
import logging
from typing import Callable

from app.repositories.database import DEFAULT_USER_ID, utcnow_iso
from app.repositories.resume_repository import ResumeRepository
from app.schemas.resume_profile import ResumeProfile
from app.services.context_trimmer import redact_pii_for_llm
from app.services.observability_service import ObservabilityService
from app.services.resume_parser import ResumeParser

logger = logging.getLogger(__name__)

# Direct-identifier fields redact_pii_for_llm strips before any LLM context
# (ADR-069). raw_text is dropped; name -> placeholder; email/location/file_name
# -> None. Used only to COUNT what was redacted for the audit event (ADR-073).
_PII_FIELDS = ("name", "email", "location", "file_name", "raw_text")


def _emit_pii_redaction(
    observability: ObservabilityService, workflow_id: str, profile: dict
) -> None:
    """Emit a `pii_redacted` security event (ADR-073) recording how many direct
    identifier fields were stripped before the profile entered LLM context.

    PII-safe by construction: logs the field NAMES and a count only, never the
    values. Severity `info` — this is a control working as designed, recorded for
    auditability, not an alarm. No-op when observability is unwired (mock-mode /
    tests) or when nothing was present to redact.
    """
    if observability is None:
        return
    present = [f for f in _PII_FIELDS if profile.get(f)]
    if not present:
        return
    observability.log_security_event(
        workflow_id=workflow_id,
        event_type="pii_redacted",
        severity="info",
        description=(
            f"Redacted {len(present)} direct identifier field(s) before LLM "
            f"context: {', '.join(present)}"
        ),
    )


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
            # ADR-070: store the REDACTED profile in state so raw_text + direct
            # identifiers never enter workflow_runs.state_json or the checkpoints
            # blob. Agents re-redact at their own seam (idempotent); the renderer
            # reads the un-redacted source from the resumes row, not from state.
            full_profile = profile.model_dump()
            _emit_pii_redaction(observability, workflow_id, full_profile)
            return {
                "resume_profile": redact_pii_for_llm(full_profile),
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
        profile = resume_parser.parse_pdf(
            str(resume_id), file_name=_resume_path.name, workflow_id=workflow_id,
            user_id=state.get("user_id") or DEFAULT_USER_ID,
        )
        logger.info("load_resume: parsed PDF %s for workflow %s", resume_id, workflow_id)

        # ADR-070: store the REDACTED profile in state (see cache-hit branch above).
        full_profile = profile.model_dump()
        _emit_pii_redaction(observability, workflow_id, full_profile)
        return {
            "resume_profile": redact_pii_for_llm(full_profile),
            "resume_version": 1,
            "current_step": "resume_profile_loading",
            "updated_at": utcnow_iso(),
        }

    return load_resume
