"""discover_jobs node — fetches, normalises, and persists jobs from all sources.

In addition to the always-on scrapers wired at startup, this node honors
state['custom_urls']: when present, it constructs a per-run CustomUrlScraper
via the injected factory and adds it to the discovery pass for this run only.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from app.repositories.database import utcnow_iso
from app.repositories.job_repository import JobRepository
from app.services.job_discovery_service import JobDiscoveryService
from app.services.observability_service import ObservabilityService
from app.workflows.limits import append_error, get_max_discovered_jobs

logger = logging.getLogger(__name__)


def make_discover_jobs_node(
    discovery_service: JobDiscoveryService,
    job_repo: JobRepository,
    observability: ObservabilityService,
    custom_url_scraper_factory: Callable[[list[str], str], Any] | None = None,
    adzuna_scraper_factory: Callable[[list[str], list[str]], Any] | None = None,
) -> Callable[[dict], dict]:
    def discover_jobs(state: dict) -> dict:
        workflow_id: str = state.get("workflow_id", "")
        search_criteria: dict = state.get("search_criteria") or {}
        custom_urls: list[str] = list(state.get("custom_urls") or [])
        errors = list(state.get("errors") or [])

        # Per-run scrapers: build one CustomUrlScraper if URLs were provided.
        extra_scrapers: list[Any] = []
        custom_scraper = None
        if custom_urls and custom_url_scraper_factory is not None:
            try:
                custom_scraper = custom_url_scraper_factory(custom_urls, workflow_id)
                extra_scrapers.append(custom_scraper)
            except Exception as exc:
                logger.warning("discover_jobs: failed to build CustomUrlScraper: %s", exc)
                errors = append_error({"errors": errors}, "custom_urls",
                                      "scraper_build_failed", str(exc), recoverable=True)

        # ADR-064: when the run carries roles, build a per-run Adzuna scraper from
        # the profile's own roles + locations and skip the senior startup Adzuna.
        # No roles -> fall back to the built-in (backward compatible).
        roles: list[str] = list(search_criteria.get("roles")
                                 or search_criteria.get("titles") or [])
        locations: list[str] = list(search_criteria.get("locations") or [])
        skip_builtin_adzuna = False
        if roles and adzuna_scraper_factory is not None:
            try:
                adzuna_scraper = adzuna_scraper_factory(roles, locations)
                if adzuna_scraper is not None:
                    extra_scrapers.append(adzuna_scraper)
                    skip_builtin_adzuna = True
            except Exception as exc:
                logger.warning("discover_jobs: failed to build per-run Adzuna scraper: %s", exc)

        try:
            postings = discovery_service.discover(
                workflow_id, search_criteria, extra_scrapers=extra_scrapers,
                skip_builtin_adzuna=skip_builtin_adzuna,
            )
            # ADR-060: manual-selection mode casts a wider net (the user triages
            # before any scoring spend); otherwise cap at MAX_JOBS_PER_RUN.
            postings = postings[:get_max_discovered_jobs(state)]
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

        # Capture per-URL custom scraper errors as workflow errors[] entries
        if custom_scraper is not None:
            for fetch_err in getattr(custom_scraper, "errors", lambda: [])():
                errors = append_error(
                    {"errors": errors},
                    step="custom_urls",
                    error_type="extraction_failed",
                    message=f"{fetch_err.get('url', '?')} → {fetch_err.get('reason', '?')}",
                    recoverable=True,
                )

        logger.info(
            "discover_jobs: found %d jobs (custom_urls=%d)",
            len(normalized_jobs), len(custom_urls),
        )
        return {
            "raw_jobs": raw_jobs,
            "normalized_jobs": normalized_jobs,
            "errors": errors,
            "current_step": "job_discovery",
            "updated_at": utcnow_iso(),
        }

    return discover_jobs
