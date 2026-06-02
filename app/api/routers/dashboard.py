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
from app.services.reads.dashboard_reads import list_scored_jobs, system_dashboard_payload

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/system")
def system_dashboard(
    days: int | None = None,
    view_user_id: str | None = None,
) -> dict:
    """Composite System Dashboard payload (ADR-075 Phase 7): all sections in one
    call. `view_user_id` is the explicit profile to view (NOT the identity seam):
    absent/empty => all profiles; a value => that profile (the drilldown).
    `days` absent => all-time."""
    uid = view_user_id or None
    return system_dashboard_payload(days=days, user_id=uid)


@router.get("/scored-jobs", response_model=DictList)
def scored_jobs(
    include_excluded: bool = False,
    user_id: str = Depends(get_current_user_id),
) -> DictList:
    """All scored jobs across the profile's runs (ADR-075 Phase 3). Unpaged — the
    analytics views aggregate the full set client-side."""
    return DictList(**list_scored_jobs(user_id=user_id, include_excluded=include_excluded))
