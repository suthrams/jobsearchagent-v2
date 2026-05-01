"""discover_jobs node — fetches, normalises, and persists jobs from all sources."""
from __future__ import annotations

import logging
from typing import Callable

from app.repositories.database import utcnow_iso
from app.repositories.job_repository import JobRepository
from app.services.job_discovery_service import JobDiscoveryService
from app.services.observability_service import ObservabilityService
from app.workflows.limits import MAX_JOBS_PER_RUN, append_error

logger = logging.getLogger(__name__)


def make_discover_jobs_node(
    discovery_service: JobDiscoveryService,
    job_repo: JobRepository,
    observability: ObservabilityService,
) -> Callable[[dict], dict]:
    def discover_jobs(state: dict) -> dict:
        workflow_id: str = state.get("workflow_id", "")
        search_criteria: dict = state.get("search_criteria") or {}
        errors = list(state.get("errors") or [])

        try:
            postings = discovery_service.discover(workflow_id, search_criteria)
            postings = postings[:MAX_JOBS_PER_RUN]
        except Exception as exc:
            logger.error("discover_jobs: discovery failed: %s", exc)
            errors = append_error(state, "job_discovery", "discovery_error", str(exc), recoverable=True,
                                  suggested_action="Check scraper config or paste job description manually.")
            return {
                "raw_jobs": [],
                "normalized_jobs": [],
                "errors": errors,
                "current_step": "job_discovery",
                "updated_at": utcnow_iso(),
            }

        raw_jobs: list[dict] = []
        normalized_jobs: list[dict] = []

        for posting in postings:
            db_dict = {
                "id": posting.job_id,
                "source": posting.source.value if hasattr(posting.source, "value") else str(posting.source),
                "source_job_id": posting.job_id,
                "title": posting.title,
                "company": posting.company,
                "location": posting.location,
                "job_description": posting.description,
                "url": posting.url,
            }
            try:
                job_repo.upsert(db_dict)
            except Exception as exc:
                logger.warning("discover_jobs: upsert failed for %s: %s", posting.job_id, exc)

            raw_jobs.append(db_dict)
            normalized_jobs.append({**db_dict, "status": "discovered"})

        logger.info("discover_jobs: found %d jobs", len(normalized_jobs))
        return {
            "raw_jobs": raw_jobs,
            "normalized_jobs": normalized_jobs,
            "errors": errors,
            "current_step": "job_discovery",
            "updated_at": utcnow_iso(),
        }

    return discover_jobs
