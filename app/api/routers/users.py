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
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.api.dependencies import get_deps, get_user_repo
from app.repositories.user_repository import UserRepository
from app.workflows.workflow_graph import WorkflowDependencies

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


class CreateUserRequest(BaseModel):
    """Onboarding step 1: the profile's identity. Note is optional human-only
    metadata. Further optional fields can be added without a contract break."""
    name: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)


@router.get("")
def list_users(repo: UserRepository = Depends(get_user_repo)) -> dict:
    """All profiles, default user (id 0) first. Backs the UI profile selector."""
    return {"users": repo.list_all()}


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
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file.file.read())
            tmp_path = tmp.name
        profile = deps.resume_parser.parse_pdf(
            tmp_path, file_name=filename, user_id=str(user_id),
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
