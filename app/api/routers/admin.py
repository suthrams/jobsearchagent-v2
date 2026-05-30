"""Admin router — operational endpoints not tied to a single workflow or profile.

Currently just the data-retention purge (ADR-070, implementing ADR-040). Purge is
explicit by design — it never runs automatically, so this endpoint (and the
equivalent tools/purge_data.py CLI) is the only trigger. Identity flows through the
ADR-062 seam for consistency, but the purge windows are global, not per-user: any
profile may trigger it (cooperative isolation; there is no auth boundary yet).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.api.identity import get_current_user_id
from app.repositories.database import DEFAULT_DB_PATH, purge_old_data
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/purge")
def purge(_user_id: str = Depends(get_current_user_id)) -> dict[str, int]:
    """Run the configured data-retention purge and return {table: rows_deleted}.

    Reads the retention windows from the effective config (yaml; the windows are
    protected keys, so they cannot be overridden per-user). Deletes rows past their
    window across the PII and observability tables, cascading a purged workflow run
    to all its child rows, and purges inactive unreferenced resumes on their own
    window. See ADR-070 / data_model.md Section 8A.
    """
    eff = ConfigService(db_path=DEFAULT_DB_PATH).get_effective_config()
    results = purge_old_data(DEFAULT_DB_PATH, config=eff)
    logger.info("admin purge complete: %s", results)
    return results
