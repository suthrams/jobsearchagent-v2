"""Dashboard / analytics read endpoints (ADR-075 Phases 3 + 7).

Cross-run, system-wide reads that are not tied to a single resource: scored-jobs
analytics (Phase 3) and the System Dashboard rollups (Phase 7). Profile-scoped via
the ADR-062 `?user_id=` seam. This is a system-metrics resource family, NOT a
UI/BFF router (ADR-075 §B).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.identity import get_current_user_id
from app.api.schemas.responses import DictList
from app.services.reads.dashboard_reads import list_scored_jobs

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/scored-jobs", response_model=DictList)
def scored_jobs(
    include_excluded: bool = False,
    user_id: str = Depends(get_current_user_id),
) -> DictList:
    """All scored jobs across the profile's runs (ADR-075 Phase 3). Unpaged — the
    analytics views aggregate the full set client-side."""
    return DictList(**list_scored_jobs(user_id=user_id, include_excluded=include_excluded))
