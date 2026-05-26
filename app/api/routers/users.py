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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import get_user_repo
from app.repositories.user_repository import UserRepository

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
