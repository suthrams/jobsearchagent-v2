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

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from app.api.decision_validation import DecisionRequest
from app.api.dependencies import get_deps
from app.api.schemas.responses import (
    ResumeClinicListResponse,
    ResumeClinicResponse,
)
from app.providers.llm_client import LLMProviderError
from app.services.resume_clinic_runner import ResumeClinicError, run_clinic
from app.services.resume_text_renderer import (
    compose_resume,
    export_content_type,
    export_file_extension,
    render as render_export,
)
from app.services.role_data import NullRoleDataProvider
from app.workflows.workflow_graph import WorkflowDependencies

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
