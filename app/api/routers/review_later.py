"""Review-later router - the "Maybe / Review later" saved-job list (ADR-100).

A bounded, per-profile, status-free bucket the user moves a relevance/clearance
filter-dropped job into, to consider later (a filter-input, NOT application
tracking). Shares the saved-jobs store with favorites (FavoriteRepository), keyed by
kind='review_later'. Path-scoped under the per-user resource family
(`/users/{user_id}/...`), like favorites. No auth - cooperative isolation.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import get_favorite_repo, get_job_repo
from app.repositories.favorite_repository import (
    KIND_REVIEW_LATER,
    FavoriteRepository,
    FavoritesCapReached,
)
from app.repositories.job_repository import JobRepository

router = APIRouter(prefix="/users", tags=["review-later"])


class AddReviewLaterRequest(BaseModel):
    """Move a job to the review-later list. The server snapshots title/company/url/
    source from the job row so the entry survives a retention purge of the source
    run and renders its link without re-reading the run."""
    workflow_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)


@router.get("/{user_id}/review-later")
def list_review_later(
    user_id: int,
    repo: FavoriteRepository = Depends(get_favorite_repo),
) -> dict:
    """A profile's review-later jobs, newest first."""
    return {"review_later": repo.list_for_user(str(user_id), kind=KIND_REVIEW_LATER)}


@router.post("/{user_id}/review-later", status_code=201)
def add_review_later(
    user_id: int,
    body: AddReviewLaterRequest,
    repo: FavoriteRepository = Depends(get_favorite_repo),
    jobs: JobRepository = Depends(get_job_repo),
) -> dict:
    """Move a job to the review-later list (idempotent). Snapshots title/company/url/
    source from the job row. 409 review_later_cap_reached past the cap; 404
    job_not_found."""
    job = jobs.get_by_id(body.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    try:
        saved = repo.add(
            str(user_id), body.workflow_id, body.job_id,
            title=job.get("title"), company=job.get("company"),
            kind=KIND_REVIEW_LATER, url=job.get("url"), source=job.get("source"),
        )
    except FavoritesCapReached as exc:
        raise HTTPException(status_code=409, detail="review_later_cap_reached") from exc
    return {"review_later": saved}


@router.delete("/{user_id}/review-later/{job_id}", status_code=204)
def remove_review_later(
    user_id: int,
    job_id: str,
    repo: FavoriteRepository = Depends(get_favorite_repo),
) -> None:
    """Remove a job from the review-later list (idempotent - 204 either way). Scoped
    to the review_later kind so it cannot delete a favorite of the same job."""
    repo.remove(str(user_id), job_id, kind=KIND_REVIEW_LATER)
