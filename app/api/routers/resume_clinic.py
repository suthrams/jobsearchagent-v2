"""Resume Clinic router (ADR-066 Phase 4) — out-of-graph resume-only operation.

Three endpoints, scoped to the active profile:

  POST   /users/{user_id}/resume-clinic              run a clinic review
  GET    /users/{user_id}/resume-clinic              list past clinic runs
  POST   /resume-clinic/{review_id}/decisions        approve / revise / reject / edit

The clinic is profile-scoped (ADR-066) and does NOT touch the LangGraph funnel.
The runner writes a lightweight workflow_runs row for cost attribution
(workflow_type="resume_clinic"; per-profile cost shows in the dashboard).
Identity is resolved by the ADR-062 seam (`get_current_user_id`); the path
`{user_id}` must match the resolved active profile, cooperative scoping.
"""
from __future__ import annotations

import logging

import json
import os
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from app.api.decision_validation import DecisionRequest
from app.api.dependencies import get_deps
from app.api.schemas.responses import (
    ResumeChatResponse,
    ResumeClinicListResponse,
    ResumeClinicResponse,
)
from app.providers.llm_client import LLMProviderError
from app.services.context_trimmer import redact_pii_for_llm
from app.services.observability_service import log_artifact_decision
from app.services.resume_clinic_runner import (
    ResumeClinicError,
    build_fidelity_context_for_overhaul,
    run_clinic,
)
from app.services.resume_text_renderer import (
    compose_resume,
    export_content_type,
    export_file_extension,
    render as render_export,
)
from app.services.role_data import NullRoleDataProvider
from app.services.tailoring_chat_seed import tailored_draft_to_overhaul
from app.workflows.limits import MAX_CHAT_TURNS_PER_CLINIC
from app.workflows.workflow_graph import WorkflowDependencies

# ADR-072: placeholder quality scorecard for a tailoring-chat session. A session
# seeded from a job's tailored draft has no clinic quality review; the chat panel
# (refine + export) does not render the scorecard, so this is never shown. The
# review_json column is NOT NULL, so a minimal valid ResumeQuality is required.
_TAILORING_CHAT_QUALITY = {
    "dimensions": [],
    "overall_summary": "Seeded from the job's tailored resume. Refine via chat and export.",
}


def _resolved_max_chat_turns() -> int:
    """MAX_CHAT_TURNS_PER_CLINIC with an optional RESUME_CHAT_MAX_TURNS env-var
    override. Used by the cap check + returned in the chat response so the UI
    can show the current effective ceiling."""
    raw = os.getenv("RESUME_CHAT_MAX_TURNS")
    if raw is None:
        return MAX_CHAT_TURNS_PER_CLINIC
    try:
        n = int(raw)
        return max(1, n)
    except ValueError:
        return MAX_CHAT_TURNS_PER_CLINIC


def _count_chat_turns_for_clinic(observability, workflow_run_id: str) -> int:
    """Count `resume_chat` llm_calls rows tagged with this clinic's
    workflow_run_id. Used to enforce the per-clinic chat-turn cap. Survives
    server restarts because the count comes from the persisted audit trail,
    not in-memory session state."""
    if not workflow_run_id:
        return 0
    try:
        rows = observability.get_llm_calls_by_run(workflow_run_id) or []
    except Exception:
        return 0
    return sum(1 for r in rows if (r.get("agent_name") or "") == "resume_chat")


def _session_cost_usd_for_clinic(observability, workflow_run_id: str) -> float:
    """Sum estimated_cost across every llm_calls row tagged with this clinic's
    workflow_run_id - reviewer + every chat turn + every fidelity call. Used
    by the chat response so the UI shows the running session cost in real time
    (not after the fact in the Cost Dashboard)."""
    if not workflow_run_id:
        return 0.0
    try:
        rows = observability.get_llm_calls_by_run(workflow_run_id) or []
    except Exception:
        return 0.0
    total = 0.0
    for r in rows:
        try:
            total += float(r.get("estimated_cost") or 0.0)
        except (TypeError, ValueError):
            continue
    return round(total, 6)

def _compact_fidelity(fidelity: dict | None) -> dict | None:
    """ADR-091: reduce a persisted FidelityReview to the few fields the chat
    agent needs to self-correct - the verdict, the recommendation, and the
    list of unsupported claims (capped). Returns None when there is no prior
    verdict, so the agent prompt's "prior fidelity" block stays absent on the
    first turn."""
    if not isinstance(fidelity, dict) or not fidelity:
        return None
    claims = fidelity.get("unsupported_claims")
    claims = (
        [str(c) for c in claims if str(c).strip()][:10]
        if isinstance(claims, list) else []
    )
    status = fidelity.get("overall_fidelity_status")
    recommendation = fidelity.get("approval_recommendation")
    if not (claims or status or recommendation):
        return None
    return {
        "status": status,
        "recommendation": recommendation,
        "unsupported_claims": claims,
    }


logger = logging.getLogger(__name__)

router = APIRouter(tags=["resume_clinic"])


class ResumeClinicRunRequest(BaseModel):
    """Run-the-clinic payload. All fields optional.

    resume_id defaults to the active profile's active resume when omitted.
    target_role / target_track left blank put the run in quality-only mode
    (the alignment axis is null).
    """
    resume_id: str | None = None
    target_role: str | None = None
    target_track: str | None = Field(default=None)
    seniority_aware: bool = False

    @field_validator("target_track")
    @classmethod
    def _validate_track(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if v not in {"ic", "architect", "management"}:
            raise ValueError("target_track must be one of: ic, architect, management")
        return v


# ── Helpers ──────────────────────────────────────────────────────────────────

def _serialize_row(row: dict) -> ResumeClinicResponse:
    return ResumeClinicResponse(
        clinic_id=row["id"],
        user_id=row["user_id"],
        resume_id=row["resume_id"],
        workflow_run_id=row.get("workflow_run_id"),
        target_role=row.get("target_role"),
        target_track=row.get("target_track"),
        seniority_aware=bool(row.get("seniority_aware")),
        quality=row.get("review"),
        alignment=row.get("alignment"),
        overhaul=row.get("overhaul"),
        fidelity_review=row.get("fidelity_review"),
        decision=row.get("decision"),
        edited=row.get("edited"),
        decided_at=row.get("decided_at"),
        created_at=row.get("created_at"),
    )


# ── Endpoints ────────────────────────────────────────────────────────────────
#
# Note: these endpoints take the acting profile from the path (`{user_id}`)
# rather than the ADR-062 query-param seam. Users-router endpoints follow the
# same pattern (e.g. PUT /users/{user_id}). Scoping is cooperative per the ADR;
# the path declares which profile the operation is for, and is not an
# authentication boundary.


@router.post("/users/{user_id}/resume-clinic", status_code=200,
             response_model=ResumeClinicResponse)
def run_resume_clinic(
    user_id: str,
    body: ResumeClinicRunRequest | None = None,
    deps: WorkflowDependencies = Depends(get_deps),
) -> ResumeClinicResponse:
    """Run a Resume Clinic review end-to-end and return the persisted row.

    Default behaviour when no body is provided: use the active resume,
    quality-only mode (no target).
    """
    payload = body or ResumeClinicRunRequest()

    # Resolve resume_id: the explicit one, or the user's active resume.
    resume_id = payload.resume_id
    if not resume_id:
        active = deps.resume_repo.get_active(str(user_id))
        if not active:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "no_active_resume",
                    "message": (
                        "No active resume for this profile. Upload a resume "
                        "or pass `resume_id` explicitly."
                    ),
                    "user_id": str(user_id),
                },
            )
        resume_id = active["id"]

    try:
        row = run_clinic(
            user_id=str(user_id),
            resume_id=resume_id,
            target_role=payload.target_role or None,
            target_track=payload.target_track or None,
            seniority_aware=bool(payload.seniority_aware),
            resume_repo=deps.resume_repo,
            clinic_repo=deps.resume_clinic_repo,
            workflow_repo=deps.workflow_repo,
            reviewer=deps.resume_reviewer,
            fidelity=deps.fidelity_reviewer,
            role_data=NullRoleDataProvider(),
            observability=deps.observability,
        )
    except ResumeClinicError as exc:
        # Unknown resume or ownership mismatch -> 404 (the entity the caller
        # asked about does not exist as far as they're concerned).
        raise HTTPException(
            status_code=404,
            detail={
                "error": "resume_not_found",
                "message": str(exc),
                "user_id": str(user_id),
                "resume_id": resume_id,
            },
        ) from exc
    except LLMProviderError as exc:
        logger.warning("run_resume_clinic: LLM failure for user=%s resume=%s: %s",
                       user_id, resume_id, exc)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "clinic_failed",
                "message": str(exc),
                "user_id": str(user_id),
                "resume_id": resume_id,
            },
        ) from exc

    return _serialize_row(row)


@router.post("/tailorings/{tailoring_id}/chat-session", status_code=200,
             response_model=ResumeClinicResponse)
def open_tailoring_chat_session(
    tailoring_id: str,
    deps: WorkflowDependencies = Depends(get_deps),
) -> ResumeClinicResponse:
    """ADR-072: open a live-chat session seeded from a job's tailored draft.

    Reuses the clinic chat + export stack. The session is a resume_clinic_reviews
    row tagged with the originating job-search run (source_workflow_run_id) + job_id
    so it lists under the job, not the clinic. Create-or-reuse: a second call for
    the same (run, job) returns the existing session so reopening preserves chat
    edits. The seed is the clicked draft's human edit (edited_json) if present, else
    the agent draft (tailored_json), converted to a clinic overhaul by
    tailored_draft_to_overhaul (deterministic, no LLM).
    """
    t = deps.tailoring_repo.get_by_id(tailoring_id)
    if t is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "tailoring_not_found",
                "message": f"Tailoring {tailoring_id!r} not found.",
                "tailoring_id": tailoring_id,
            },
        )
    job_id = t.get("job_id")
    resume_id = t.get("resume_id")
    source_run = t.get("workflow_run_id")

    # Reuse an existing session for this (run, job) so reopening keeps chat edits.
    existing = deps.resume_clinic_repo.list_by_job(source_run, job_id)
    if existing:
        return _serialize_row(existing[0])

    resume = deps.resume_repo.get_by_id(resume_id)
    if resume is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "resume_not_found",
                "message": f"Resume {resume_id!r} for tailoring {tailoring_id!r} not found.",
                "tailoring_id": tailoring_id,
            },
        )
    user_id = str(resume.get("user_id") or "0")

    # Seed from the clicked draft (Q1: edited_json if human-edited, else agent draft).
    draft = t.get("edited") or t.get("tailored") or {}
    overhaul = tailored_draft_to_overhaul(draft).model_dump()

    clinic_id = str(uuid.uuid4())
    cost_run_id = str(uuid.uuid4())
    # Cost-correlation run row (mirrors run_clinic): correlates chat + fidelity
    # llm_calls for the session-cost meter and the per-profile Cost Dashboard.
    deps.workflow_repo.create(cost_run_id, "resume_clinic", {
        "status": "completed",
        "current_step": "tailoring_chat",
        "user_id": user_id,
        "resume_id": resume_id,
    })
    deps.resume_clinic_repo.create(
        clinic_id, user_id, resume_id,
        workflow_run_id=cost_run_id,
        target_role=None, target_track=None, seniority_aware=False,
        review=dict(_TAILORING_CHAT_QUALITY),
        alignment=None,
        overhaul=overhaul,
        fidelity_review=None,
        source_workflow_run_id=source_run,
        job_id=job_id,
    )
    row = deps.resume_clinic_repo.get_by_id(clinic_id)
    return _serialize_row(row or {})


@router.get("/users/{user_id}/resume-clinic", response_model=ResumeClinicListResponse)
def list_resume_clinic_runs(
    user_id: str,
    deps: WorkflowDependencies = Depends(get_deps),
) -> ResumeClinicListResponse:
    rows = deps.resume_clinic_repo.list_by_user(str(user_id))
    return ResumeClinicListResponse(
        user_id=str(user_id),
        reviews=[_serialize_row(r) for r in rows],
    )


@router.post("/resume-clinic/{review_id}/decisions", status_code=200,
             response_model=ResumeClinicResponse)
def submit_resume_clinic_decision(
    review_id: str,
    body: DecisionRequest,
    deps: WorkflowDependencies = Depends(get_deps),
) -> ResumeClinicResponse:
    row = deps.resume_clinic_repo.get_by_id(review_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "clinic_review_not_found",
                "message": f"Resume clinic review {review_id!r} not found.",
                "review_id": review_id,
            },
        )
    deps.resume_clinic_repo.set_decision(review_id, body.approval, edited=body.edited)
    # ADR-074 Gap 1: mirror the decision into the human_decisions audit trail.
    log_artifact_decision(
        deps.observability,
        workflow_id=row.get("workflow_run_id"),
        decision_type="resume_clinic",
        decision_value=body.approval,
        presented_at=row.get("created_at"),
        payload={"review_id": review_id, "job_id": row.get("job_id"),
                 "edited": bool(body.edited)},
    )
    updated = deps.resume_clinic_repo.get_by_id(review_id)
    return _serialize_row(updated or row)


# ── Resume text export (ADR-066 fast-follow) ─────────────────────────────────

_SUPPORTED_FORMATS = {"md", "txt", "html", "json", "docx", "pdf"}


@router.get("/resume-clinic/{review_id}/export")
def export_resume_clinic_text(
    review_id: str,
    format: str = "md",
    deps: WorkflowDependencies = Depends(get_deps),
) -> Response:
    """Render a clinic review's final resume in the requested format.

    Decision-aware: `approve` -> apply the agent's overhaul; `edit` -> use the
    human-authored draft; `reject` -> render the original resume unchanged;
    `revise` / no decision -> render a preview banner-tagged version. The
    renderer is deterministic - no LLM call.

    `?format=` must be one of md, txt, html, json, docx, pdf. Returns raw
    bytes with the appropriate Content-Type and a download-friendly
    Content-Disposition header.
    """
    fmt = (format or "").strip().lower()
    if fmt not in _SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_format",
                "message": (
                    f"Format {format!r} is not supported. "
                    f"Pick one of: {sorted(_SUPPORTED_FORMATS)}."
                ),
            },
        )

    row = deps.resume_clinic_repo.get_by_id(review_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "clinic_review_not_found",
                "message": f"Resume clinic review {review_id!r} not found.",
                "review_id": review_id,
            },
        )

    resume = deps.resume_repo.get_by_id(row.get("resume_id"))
    if resume is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "resume_not_found",
                "message": "The resume this clinic review points at is missing.",
                "resume_id": row.get("resume_id"),
            },
        )

    # parsed_profile_json is the renderer's source. Repos return it as the
    # serialized string column; decode here so the renderer sees a dict.
    raw_profile = resume.get("parsed_profile_json")
    if isinstance(raw_profile, str):
        try:
            profile_dict = json.loads(raw_profile)
        except Exception:
            profile_dict = {}
    elif isinstance(raw_profile, dict):
        profile_dict = raw_profile
    else:
        profile_dict = {}

    rendered = compose_resume(
        profile=profile_dict,
        overhaul=row.get("overhaul"),
        edited=row.get("edited"),
        decision=row.get("decision"),
    )

    payload = render_export(fmt, rendered)
    ext = export_file_extension(fmt)
    filename = f"resume_clinic_{review_id[:8]}.{ext}"
    return Response(
        content=payload,
        media_type=export_content_type(fmt),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ── Chat-revise loop (ADR-068) ───────────────────────────────────────────────


_CHAT_SECTION_FOCUS = {"whole", "summary", "experience", "skills",
                       "education", "certifications"}


class ResumeChatRequest(BaseModel):
    """One turn of the chat-revise loop.

    `section` is a hint to the agent ("focus on the Experience section") -
    enforcement is in the agent prompt + the Fidelity Reviewer, not here.
    `history` is the in-session conversation kept by the UI and submitted on
    each turn; the backend does not persist it.
    """
    message: str = Field(min_length=1, max_length=2000)
    section: Literal[
        "whole", "headline", "summary", "experience", "skills",
        "education", "certifications",
    ] = "whole"
    history: list[dict] = Field(default_factory=list)

    @field_validator("history")
    @classmethod
    def _validate_history(cls, v: list[dict]) -> list[dict]:
        # Cap the submitted history at the last 10 turns so a runaway client
        # cannot blow the prompt budget. The agent only needs short context.
        return v[-10:] if v else []


@router.post("/resume-clinic/{review_id}/chat", status_code=200,
             response_model=ResumeChatResponse)
def chat_resume_clinic(
    review_id: str,
    body: ResumeChatRequest,
    deps: WorkflowDependencies = Depends(get_deps),
) -> ResumeChatResponse:
    """Run one chat-revise turn against a clinic review.

    Loads the resume and the current overhaul (the chat-edited state if the
    user has revised before, otherwise the agent's original overhaul), calls
    the ResumeChatAgent, runs the Fidelity Reviewer on the new rewrites, and
    persists the new overhaul into `edited_json`. The `decision` field is NOT
    changed - that is a separate explicit user action (Save final edit /
    Reject).
    """
    row = deps.resume_clinic_repo.get_by_id(review_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "clinic_review_not_found",
                "message": f"Resume clinic review {review_id!r} not found.",
                "review_id": review_id,
            },
        )

    # ── Cost cap (ADR-068 cost-monitoring follow-up, 2026-05-29) ─────────────
    # Block before any LLM call - count the past `resume_chat` rows tagged
    # with this clinic's workflow_run_id and refuse if we're at the cap.
    # The chat agent and the fidelity reviewer both write through
    # ObservabilityService.log_llm_call; the read uses the public pass-through
    # method get_llm_calls_by_run on the same service.
    max_turns = _resolved_max_chat_turns()
    workflow_run_id = row.get("workflow_run_id") or ""
    turns_used = _count_chat_turns_for_clinic(deps.observability, workflow_run_id)
    if turns_used >= max_turns:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "chat_turn_cap_reached",
                "message": (
                    f"This clinic review has used all {max_turns} chat turns "
                    f"({turns_used} on record). Click 'Save final edit' to lock the "
                    f"current state, or 'Discard chat edits' and run a fresh clinic. "
                    f"Override at the operator level via the RESUME_CHAT_MAX_TURNS "
                    f"env var."
                ),
                "review_id": review_id,
                "turns_used": turns_used,
                "max_turns": max_turns,
            },
        )

    resume = deps.resume_repo.get_by_id(row.get("resume_id"))
    if resume is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "resume_not_found",
                "message": "The resume this clinic review points at is missing.",
                "resume_id": row.get("resume_id"),
            },
        )

    raw_profile = resume.get("parsed_profile_json")
    if isinstance(raw_profile, str):
        try:
            parsed_profile = json.loads(raw_profile)
        except Exception:
            parsed_profile = {}
    elif isinstance(raw_profile, dict):
        parsed_profile = raw_profile
    else:
        parsed_profile = {}
    raw_text = resume.get("raw_text") or ""

    # Prefer the chat-edited state when populated (the user has already
    # revised in this session); otherwise the agent's original overhaul.
    current_overhaul: dict = row.get("edited") or row.get("overhaul") or {}

    # ADR-091: feed the PRIOR fidelity verdict back into the agent so it can
    # self-correct. Without this the reviewer flagged the same unsupported
    # claims every turn and the agent never saw them - the loop never
    # converged. Compact (status + recommendation + unsupported claims) so it
    # adds little to the prompt budget.
    prior_fidelity = _compact_fidelity(row.get("fidelity_review"))

    # Build the agent context. The parsed profile is cached so subsequent
    # turns hit the second cached prompt block at the 10% rate.
    # redact_pii_for_llm drops raw_text AND direct identifiers (ADR-069): the
    # chat agent never needs them, and only the Fidelity Reviewer sees raw_text
    # (passed top-level in build_fidelity_context_for_overhaul).
    chat_context: dict = {
        "_cached": {"resume_profile": redact_pii_for_llm(parsed_profile)},
        "resume_id": resume.get("id"),
        "current_overhaul": current_overhaul,
        "prior_fidelity": prior_fidelity,
        "history": [
            {"role": h.get("role"), "message": h.get("message")}
            for h in (body.history or [])
            if isinstance(h, dict) and h.get("message")
        ],
        "section": body.section,
        "message": body.message,
    }

    workflow_run_id = row.get("workflow_run_id") or ""

    try:
        result = deps.resume_chat.run(workflow_run_id, chat_context)
    except LLMProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "chat_failed",
                "message": str(exc),
                "review_id": review_id,
            },
        ) from exc

    new_overhaul = result.overhaul.model_dump()
    rewrites = new_overhaul.get("rewrites") or []

    # Fidelity invariant (ADR-066 carried into ADR-068): always run on the FULL
    # set of rewrites every clinic call, skipped only when there are no
    # rewrites. Reviewing the whole draft each turn (not just the changed
    # bullets) is deliberate: a fabricated claim authored two turns ago must
    # stay flagged until it is actually fixed, and the persisted verdict must
    # describe the WHOLE resume the user is about to export - not just the last
    # delta. Fidelity is the cheap (Haiku) leg; the cost lever for the chat
    # loop is fewer turns via the prior_fidelity self-correction feedback
    # (ADR-091), not narrowing this review. A reviewer LLM failure persists the
    # turn with null fidelity rather than failing the chat.
    fidelity_dict: dict | None = None
    if rewrites:
        try:
            fidelity_result = deps.fidelity_reviewer.run(
                workflow_run_id,
                build_fidelity_context_for_overhaul(
                    clinic_id=review_id,
                    resume_id=resume.get("id") or "",
                    parsed_profile=parsed_profile,
                    raw_text=raw_text,
                    rewrites=rewrites,
                ),
            )
            fidelity_dict = fidelity_result.model_dump()
        except LLMProviderError:
            fidelity_dict = None

    # Persist. decision is unchanged - that's a separate user action.
    deps.resume_clinic_repo.set_edited(
        review_id, new_overhaul, fidelity_review=fidelity_dict,
    )

    # ── Cost rollup (computed AFTER persist so this turn's llm_calls rows
    #    are included in the totals returned to the UI) ────────────────────
    turns_used_now = _count_chat_turns_for_clinic(deps.observability, workflow_run_id)
    session_cost_usd = _session_cost_usd_for_clinic(deps.observability, workflow_run_id)

    return ResumeChatResponse(
        reply=result.reply,
        overhaul=new_overhaul,
        fidelity_review=fidelity_dict,
        changed_sections=list(result.changed_sections),
        turns_used=turns_used_now,
        max_turns=max_turns,
        session_cost_usd=session_cost_usd,
    )


@router.post("/resume-clinic/{review_id}/discard-edits", status_code=200)
def discard_resume_clinic_edits(
    review_id: str,
    deps: WorkflowDependencies = Depends(get_deps),
) -> dict:
    """Revert the chat-edited state. Clears edited_json, decision, and
    decided_at so the renderer falls back to the agent's original overhaul.
    The agent's original overhaul_json is intact.
    """
    row = deps.resume_clinic_repo.get_by_id(review_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "clinic_review_not_found",
                "message": f"Resume clinic review {review_id!r} not found.",
                "review_id": review_id,
            },
        )
    deps.resume_clinic_repo.discard_edits(review_id)
    return {"cleared": True, "review_id": review_id}
