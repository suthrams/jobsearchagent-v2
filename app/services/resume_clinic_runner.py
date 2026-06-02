"""Resume Clinic runner (ADR-066 Phase 3) — out-of-graph orchestration.

`run_clinic` ties together resume load, role-data lookup (stubbed in v1),
ResumeReviewerAgent, FidelityReviewer on the rewrites, and persistence.

The clinic does NOT enter the LangGraph funnel. It writes a lightweight
`workflow_runs` row (workflow_type="resume_clinic") used only as the
correlation id for `llm_calls` and `agent_events`, so clinic spend is
attributed to the profile and shows up in the per-profile Cost Dashboard
(ADR-062). No discovery, no scoring, no interrupt.

Fidelity invariant (ADR-015 / 056 / 059): the Fidelity Reviewer runs on the
agent-authored rewrites every call. The reviewer polices the agent, never the
human (a later `edit` decision is owner-authored and is not re-reviewed).

Translation note: the existing FidelityReviewer prompt is tailoring-tuned (it
expects `tailored_draft` shaped like `TailoredResumeDraft`, with fields like
`impact_rationale` and `overall_tailoring_notes` that the clinic does not
produce). We translate clinic `RewriteSuggestion`s into a `TailoredResumeDraft`
envelope with placeholder values for the tailoring-specific fields so the
fidelity agent receives a valid input and its core fabrication/evidence checks
operate. The output is persisted as-is for transparency; the UI shows the
verdict and the fabrication/evidence flags (the load-bearing fields). A
clinic-tuned fidelity prompt is a documented fast-follow.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from app.agents.fidelity_reviewer import FidelityReviewer
from app.agents.resume_reviewer import ResumeReviewerAgent
from app.providers.llm_client import LLMProviderError
from app.repositories.resume_clinic_repository import ResumeClinicRepository
from app.repositories.resume_repository import ResumeRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.services.context_trimmer import redact_pii_for_llm
from app.services.role_data import RoleDataProvider

logger = logging.getLogger(__name__)


class ResumeClinicError(Exception):
    """Raised when the runner cannot proceed (missing/foreign resume, etc.).

    Distinct from LLMProviderError (which the runner re-raises) so the API
    layer can map this to a 404 and the LLM failure to a 502.
    """


def run_clinic(
    user_id: str,
    resume_id: str,
    *,
    target_role: str | None,
    target_track: str | None,
    seniority_aware: bool,
    resume_repo: ResumeRepository,
    clinic_repo: ResumeClinicRepository,
    workflow_repo: WorkflowRepository,
    reviewer: ResumeReviewerAgent,
    fidelity: FidelityReviewer,
    role_data: RoleDataProvider,
) -> dict:
    """Run one clinic review end-to-end and return the persisted row.

    Returns the row as a dict (the same shape `ResumeClinicRepository.get_by_id`
    returns: JSON columns unpacked, seniority_aware cast to bool).
    """
    # 1) Load the resume; enforce ownership.
    resume = resume_repo.get_by_id(resume_id)
    if resume is None:
        raise ResumeClinicError(f"resume {resume_id!r} not found")
    if str(resume.get("user_id") or "0") != str(user_id):
        raise ResumeClinicError(
            f"resume {resume_id!r} is not owned by user {user_id!r}"
        )

    raw_text = resume.get("raw_text") or ""
    parsed_profile_raw = resume.get("parsed_profile_json")
    try:
        parsed_profile = json.loads(parsed_profile_raw) if parsed_profile_raw else {}
    except Exception:
        parsed_profile = {}

    # 2) Generate ids and write the lightweight workflow_runs row for cost
    #    attribution. Status starts at "running"; flipped to "completed" at the
    #    end of a successful run. The row is the correlation id for llm_calls
    #    and agent_events the reviewer + fidelity will write through
    #    BaseAgent.
    clinic_id = str(uuid.uuid4())
    workflow_run_id = str(uuid.uuid4())
    initial_state = {
        "status": "running",
        "current_step": "resume_clinic",
        "user_id": str(user_id),
        "resume_id": resume_id,
    }
    workflow_repo.create(workflow_run_id, "resume_clinic", initial_state)

    # 3) Role-data lookup (None in v1; never raises per the provider contract).
    try:
        role = role_data.lookup(target_role, target_track)
    except Exception as exc:
        logger.warning("run_clinic: role_data.lookup raised (should not); ignoring: %s", exc)
        role = None

    # 4) Build reviewer context. resume_profile is moved into "_cached" so
    #    PromptLoader puts it in a second cached system block (10% rate on
    #    subsequent calls within the 5-min window). redact_pii_for_llm drops
    #    raw_text AND the direct identifiers (ADR-069): the reviewer never needs
    #    them, and raw_text goes to fidelity only (ADR-015).
    reviewer_context: dict[str, Any] = {
        "_cached": {"resume_profile": redact_pii_for_llm(parsed_profile)},
        "resume_id": resume_id,
        "target_role": target_role,
        "target_track": target_track,
        "seniority_aware": bool(seniority_aware),
        "role_data": role.model_dump() if role is not None else None,
    }

    # 5) Run the reviewer. LLM failure flips the workflow row to "failed" and
    #    re-raises so the API can return 502.
    try:
        review = reviewer.run(workflow_run_id, reviewer_context)
    except LLMProviderError as exc:
        workflow_repo.update_state(workflow_run_id, {
            **initial_state, "status": "failed",
            "errors": [{"message": str(exc)}],
        })
        raise

    # 6) Run fidelity on the rewrites. Always — never skip on the agent path.
    fidelity_dict: dict | None = None
    if review.rewrites:
        try:
            fidelity_result = fidelity.run(workflow_run_id, build_fidelity_context_for_overhaul(
                clinic_id=clinic_id,
                resume_id=resume_id,
                parsed_profile=parsed_profile,
                raw_text=raw_text,
                rewrites=review.rewrites,
            ))
            fidelity_dict = fidelity_result.model_dump()
        except LLMProviderError as exc:
            # Persist the review anyway so the user can still see the quality
            # scorecard + alignment + reorganization plan; fidelity unavailable.
            logger.warning("run_clinic: FidelityReviewer failed for %s/%s: %s",
                           workflow_run_id, clinic_id, exc)
            fidelity_dict = None

    # 7) Persist the clinic row.
    review_dict = review.model_dump()
    quality = review_dict.get("quality") or {}
    alignment = review_dict.get("alignment")
    overhaul = {
        "reorganization": review_dict.get("reorganization") or {},
        "rewrites": review_dict.get("rewrites") or [],
    }
    clinic_repo.create(
        clinic_id,
        str(user_id),
        resume_id,
        workflow_run_id=workflow_run_id,
        target_role=target_role,
        target_track=target_track,
        seniority_aware=bool(seniority_aware),
        review=quality,
        alignment=alignment,
        overhaul=overhaul,
        fidelity_review=fidelity_dict,
    )

    # 8) Flip workflow run to completed.
    workflow_repo.update_state(workflow_run_id, {
        **initial_state, "status": "completed",
        "current_step": "completed",
    })

    return clinic_repo.get_by_id(clinic_id) or {"id": clinic_id}


# ── helpers ──────────────────────────────────────────────────────────────────

# Clinic rewrite claim_type -> tailoring claim_type (the fidelity prompt's
# expected vocabulary). The clinic enum is more granular; mapping collapses to
# the tailoring closest-equivalent so the fidelity prompt's checks operate.
# Maps every clinic value to "reword" or "emphasize"; gap and remove are not
# clinic concepts (the clinic never fabricates and never removes).
_CLAIM_TYPE_MAP = {
    "restate":  "reword",
    "reorder":  "reword",
    "quantify": "emphasize",
    "reframe":  "emphasize",
}


def build_fidelity_context_for_overhaul(
    *, clinic_id: str, resume_id: str,
    parsed_profile: dict, raw_text: str,
    rewrites,
) -> dict:
    """Pack a clinic review's rewrites into the tailoring-shaped envelope the
    FidelityReviewer prompt expects. Tailoring-specific fields receive
    placeholder values (impact_rationale, overall_tailoring_notes, etc.). The
    core evidence-binding and fabrication checks run on the real fields
    (original_text, suggested_text, supporting_evidence).

    Public (ADR-068) so the chat-revise endpoint can reuse the same glue.
    Callable signature unchanged from the original private helper.
    """
    tailored_bullets = []
    for r in rewrites:
        # `r` is a RewriteSuggestion (pydantic) or a dict.
        rd = r.model_dump() if hasattr(r, "model_dump") else dict(r)
        tailored_bullets.append({
            "original_text":       rd.get("original_text", ""),
            "suggested_text":      rd.get("suggested_text", ""),
            "supporting_evidence": rd.get("supporting_evidence", ""),
            "claim_type":          _CLAIM_TYPE_MAP.get(rd.get("claim_type", ""), "reword"),
            "fidelity_risk":       "low",
            "section_label":       rd.get("section_label", ""),
            # impact_rationale is JD-relative and meaningless in a job-agnostic
            # clinic review. The fidelity prompt will flag this; the clinic-
            # mode follow-up will give the prompt a way to ignore it.
            "impact_rationale":    "Resume Clinic rewrite; no job context.",
            "unsupported_claims":  [],
        })
    # Pull the parsed_profile into _cached so the chat-revise loop's repeated
    # fidelity calls (one per turn) read it back at 10% rate within the 5-min
    # window. Each turn re-uses the same redacted profile bytes, so the cache
    # key matches the chat agent's own cached prefix (also keyed on the
    # parsed_profile). redact_pii_for_llm drops raw_text + direct identifiers
    # from the cached block (ADR-069); raw_text is kept TOP-LEVEL below, the one
    # sanctioned path that gives fidelity the resume text for evidence-binding
    # (it must NOT be in the cached block - the chat and fidelity agents are
    # different prompts; cache keys are per-prompt).
    return {
        "_cached": {"resume_profile": redact_pii_for_llm(parsed_profile)},
        # job_id is required by the fidelity schema. Use a synthetic
        # "clinic:<clinic_id>" so the audit trail clearly identifies the
        # review's source as a clinic run rather than a job tailoring.
        "job_id": f"clinic:{clinic_id}",
        "resume_id": resume_id,
        "job_description": "",
        "tailored_draft": {
            "job_id": f"clinic:{clinic_id}",
            "resume_id": resume_id,
            "headline_suggestions":           [],
            "summary_suggestions":            [],
            "experience_bullet_suggestions":  tailored_bullets,
            "skills_section_suggestions":     [],
            "overall_tailoring_notes":        "Resume Clinic overhaul; no job context.",
            "fidelity_risk_summary":          "",
        },
        # Raw text in case a future fidelity prompt switches to checking against
        # raw_text directly (the clinic's source of truth for evidence-binding).
        "raw_text": raw_text,
    }
