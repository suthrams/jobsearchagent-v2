"""Favorites router - "My favorite jobs" (ADR-090).

A bounded, per-profile, status-free working set of jobs the user flags to tailor
toward (a filter-input, NOT application tracking). Path-scoped under the per-user
resource family (`/users/{user_id}/...`, the ADR-066 documented exception to the
ADR-062 `?user_id=` seam). No auth - cooperative isolation, like the rest of /users.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import get_favorite_repo, get_job_repo
from app.repositories.favorite_repository import FavoriteRepository, FavoritesCapReached
from app.repositories.job_repository import JobRepository

router = APIRouter(prefix="/users", tags=["favorites"])


class AddFavoriteRequest(BaseModel):
    """Favorite a scored job. The server snapshots title/company from the job row so
    the favorite survives a retention purge of the source run."""
    workflow_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)


@router.get("/{user_id}/favorites")
def list_favorites(
    user_id: int,
    repo: FavoriteRepository = Depends(get_favorite_repo),
) -> dict:
    """A profile's favorites, newest first. Backs the Matches/Opportunity stars and
    the Resume Clinic focus dropdown."""
    return {"favorites": repo.list_for_user(str(user_id))}


@router.post("/{user_id}/favorites", status_code=201)
def add_favorite(
    user_id: int,
    body: AddFavoriteRequest,
    repo: FavoriteRepository = Depends(get_favorite_repo),
    jobs: JobRepository = Depends(get_job_repo),
) -> dict:
    """Favorite a job (idempotent). Snapshots title/company from the job row.
    409 favorites_cap_reached past the per-profile cap; 404 job_not_found."""
    job = jobs.get_by_id(body.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    try:
        fav = repo.add(
            str(user_id), body.workflow_id, body.job_id,
            title=job.get("title"), company=job.get("company"),
        )
    except FavoritesCapReached as exc:
        raise HTTPException(status_code=409, detail="favorites_cap_reached") from exc
    return {"favorite": fav}


@router.delete("/{user_id}/favorites/{job_id}", status_code=204)
def remove_favorite(
    user_id: int,
    job_id: str,
    repo: FavoriteRepository = Depends(get_favorite_repo),
) -> None:
    """Un-favorite a job (idempotent - 204 whether or not it was favorited)."""
    repo.remove(str(user_id), job_id)
