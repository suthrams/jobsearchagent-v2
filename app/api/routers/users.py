"""Users router — GET /users and POST /users (ADR-062).

Profiles are the multi-user identities. The UI profile selector lists them
(GET) and the onboarding wizard creates them (POST). A profile is just an
identity row; resume, config, and history are scoped to it elsewhere.

There is no authentication: creating/listing profiles is open, consistent with
the cooperative-isolation model. The seam in app/api/identity.py is where a real
access boundary attaches later.
"""
from __future__ import annotations

import logging
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.api.dependencies import get_deps, get_user_repo
from app.api.schemas.responses import ResumeList
from app.repositories.user_repository import UserRepository
from app.services.reads.user_reads import get_resume_profile, list_user_resumes
from app.workflows.workflow_graph import WorkflowDependencies

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


class CreateUserRequest(BaseModel):
    """Onboarding step 1: the profile's identity. Note is optional human-only
    metadata. Further optional fields can be added without a contract break."""
    name: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)


class UpdateUserRequest(BaseModel):
    """Edit a profile's display name / note. The id is never changed."""
    name: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)


@router.get("")
def list_users(repo: UserRepository = Depends(get_user_repo)) -> dict:
    """All profiles, default user (id 0) first. Backs the UI profile selector."""
    return {"users": repo.list_all()}


@router.get("/{user_id}/resumes", response_model=ResumeList)
def list_user_resumes_endpoint(user_id: int) -> ResumeList:
    """A profile's resumes, active first then newest (ADR-075 Phase 2). Path-scoped
    to the profile, matching the users-router convention. Returns the §B.1
    envelope (unpaged — a profile has few resumes)."""
    return ResumeList(**list_user_resumes(str(user_id)))


@router.get("/{user_id}/resumes/{resume_id}/profile")
def get_resume_profile_endpoint(user_id: int, resume_id: str) -> dict:
    """Parsed profile for one resume (ADR-075 Phase 8) — backs the tailoring/clinic
    chat live preview. Returns {"profile": {...}} ({} if absent)."""
    return {"profile": get_resume_profile(resume_id)}


@router.post("", status_code=201)
def create_user(
    body: CreateUserRequest,
    repo: UserRepository = Depends(get_user_repo),
) -> dict:
    """Create a new profile and return it. The id is assigned by the database
    (auto-increment from 1; 0 is the reserved pre-existing-data profile)."""
    name = body.name.strip()
    if not name:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_name", "message": "name must not be blank."},
        )
    try:
        new_id = repo.create(name, note=(body.note.strip() if body.note else None))
    except Exception as exc:
        logger.exception("create_user failed for name=%s", name)
        raise HTTPException(
            status_code=500,
            detail={"error": "persist_failed", "message": str(exc)},
        ) from exc
    created = repo.get_by_id(new_id)
    return {"user": created}


@router.put("/{user_id}")
def update_user(
    user_id: int,
    body: UpdateUserRequest,
    repo: UserRepository = Depends(get_user_repo),
) -> dict:
    """Update a profile's display name / note. Returns the updated profile."""
    if not repo.exists(user_id):
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_user", "message": f"No profile with id {user_id}."},
        )
    name = body.name.strip()
    if not name:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_name", "message": "name must not be blank."},
        )
    try:
        repo.update(user_id, name, note=((body.note or "").strip() or None))
    except Exception as exc:
        logger.exception("update_user failed for id=%s", user_id)
        raise HTTPException(
            status_code=500,
            detail={"error": "persist_failed", "message": str(exc)},
        ) from exc
    return {"user": repo.get_by_id(user_id)}


@router.delete("/{user_id}", status_code=200)
def delete_user(
    user_id: int,
    repo: UserRepository = Depends(get_user_repo),
) -> dict:
    """Delete a profile and cascade its OWNED working data (resumes, config,
    memory, clinic reviews, saved jobs). Workflow history + telemetry are preserved
    (orphaned; reads COALESCE to profile 0). Profile 0 (pre-existing data) cannot be
    deleted -> 403. Returns per-table deletion counts."""
    if not repo.exists(user_id):
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_user", "message": f"No profile with id {user_id}."},
        )
    try:
        counts = repo.delete(user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "profile_not_deletable", "message": str(exc)},
        ) from exc
    except Exception as exc:
        logger.exception("delete_user failed for id=%s", user_id)
        raise HTTPException(
            status_code=500,
            detail={"error": "persist_failed", "message": str(exc)},
        ) from exc
    return {"user_id": str(user_id), "deleted": counts}


@router.post("/{user_id}/resume", status_code=201)
def upload_resume(
    user_id: int,
    file: UploadFile = File(...),
    repo: UserRepository = Depends(get_user_repo),
    deps: WorkflowDependencies = Depends(get_deps),
) -> dict:
    """Onboarding step 2: upload a PDF resume for a profile.

    Saves the upload to a temp file, parses it via ResumeParser (which stores the
    parsed profile under this user_id and marks it the profile's active resume),
    then deletes the temp file. Returns the new resume id.
    """
    if not repo.exists(user_id):
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_user", "message": f"No profile with id {user_id}."},
        )
    filename = file.filename or "resume.pdf"
    suffix = Path(filename).suffix or ".pdf"
    tmp_path: str | None = None
    # ADR-074 minor: attribute the parse LLM call to a lightweight correlation
    # run (same pattern as the Resume Clinic, ADR-066) so its cost is visible in
    # the System Dashboard scoped to this profile, instead of an orphaned call
    # that COALESCEs to user "0". A cache hit writes no llm_call, so the row is
    # simply unused then.
    correlation_run_id = str(uuid.uuid4())
    deps.workflow_repo.create(
        correlation_run_id, "resume_upload",
        {"status": "completed", "current_step": "resume_upload",
         "user_id": str(user_id)},
    )
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file.file.read())
            tmp_path = tmp.name
        profile = deps.resume_parser.parse_pdf(
            tmp_path, file_name=filename, user_id=str(user_id),
            workflow_id=correlation_run_id,
        )
    except Exception as exc:
        logger.exception("upload_resume failed for user_id=%s", user_id)
        raise HTTPException(
            status_code=422,
            detail={"error": "resume_parse_failed", "message": str(exc)},
        ) from exc
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return {
        "resume_id": profile.resume_id,
        "file_name": profile.file_name,
        "name": profile.name,
    }


@router.delete("/{user_id}/resume/{resume_id}", status_code=200)
def delete_resume(
    user_id: int,
    resume_id: str,
    repo: UserRepository = Depends(get_user_repo),
    deps: WorkflowDependencies = Depends(get_deps),
) -> dict:
    """Delete a resume and cascade to its Resume Clinic reviews.

    The resume's job-search workflow_runs are preserved as historical cost
    data; only the clinic reviews are cascaded (they reference a resume that
    would otherwise no longer exist).

    Returns 404 when the resume is unknown OR is owned by a different
    profile (we don't distinguish - same cooperative-scoping rule as the
    rest of ADR-062). Returns 200 with `{resume_deleted, clinic_reviews_deleted}`
    so the UI can surface the cascade impact.
    """
    if not repo.exists(user_id):
        raise HTTPException(
            status_code=404,
            detail={
                "error": "unknown_user",
                "message": f"No profile with id {user_id}.",
                "user_id": user_id,
            },
        )

    # Cascade clinic reviews FIRST so a partial failure leaves a still-renderable
    # state (resume present, no orphaned clinic rows). The two deletes are not
    # atomic across the two repos; the cascade is small enough that this is
    # acceptable.
    clinic_deleted = deps.resume_clinic_repo.delete_by_resume(
        resume_id, str(user_id),
    )
    resume_deleted = deps.resume_repo.delete(resume_id, str(user_id))

    if resume_deleted == 0:
        # Either the resume id was unknown or it was owned by someone else.
        # Same response either way to keep the scoping cooperative.
        raise HTTPException(
            status_code=404,
            detail={
                "error": "resume_not_found",
                "message": (
                    f"Resume {resume_id!r} is not present on profile #{user_id}."
                ),
                "user_id": user_id,
                "resume_id": resume_id,
            },
        )

    return {
        "resume_deleted": resume_deleted,
        "clinic_reviews_deleted": clinic_deleted,
        "user_id": user_id,
        "resume_id": resume_id,
    }
