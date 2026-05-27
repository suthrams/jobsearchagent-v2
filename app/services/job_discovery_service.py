"""JobDiscoveryService — wraps v1 scrapers, normalises output to JobPosting, deduplicates.

discover() does NOT persist. It returns list[JobPosting].
The orchestrator writes results via JobRepository.
"""
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from app.repositories.database import utcnow_iso
from app.repositories.job_repository import JobRepository
from app.schemas.job_posting import JobPosting, JobSource, SalaryInfo, WorkMode
from models.filters import EXCLUDED_TITLE_KEYWORDS

logger = logging.getLogger(__name__)

# Hard cap: no single scraper may block the workflow longer than this.
# With ConcurrentAdzunaScraper (5 workers, ~7s/task), 62 calls ≈ 90s typical.
_SCRAPER_TIMEOUT_S = 180

_SOURCE_MAP: dict[str, JobSource] = {
    "linkedin": JobSource.LINKEDIN,
    "adzuna": JobSource.ADZUNA,
    "ladders": JobSource.LADDERS,
}

_WORK_MODE_MAP: dict[str, WorkMode] = {
    "remote": WorkMode.REMOTE,
    "hybrid": WorkMode.HYBRID,
    "onsite": WorkMode.ONSITE,
}


class JobDiscoveryService:
    """
    Coordinates v1 scrapers, normalises to JobPosting, deduplicates by URL,
    and caps results at max_jobs_per_run.

    Args:
        job_repository : used for URL deduplication against the DB
        config         : effective config dict from ConfigService
        scrapers       : list of pre-instantiated v1 BaseScraper instances.
                         Pass mock scrapers in tests; orchestrator passes real ones.
    """

    def __init__(
        self,
        job_repository: JobRepository,
        config: dict,
        scrapers: list[Any] | None = None,
    ) -> None:
        self._repo = job_repository
        self._max_jobs: int = config.get("search", {}).get("max_jobs", 20)
        self._scrapers: list[Any] = scrapers or []

    def discover(
        self,
        workflow_id: str,
        search_criteria: dict,
        extra_scrapers: list[Any] | None = None,
        skip_builtin_adzuna: bool = False,
        max_years_experience: int | None = None,
    ) -> list[JobPosting]:
        """Run all scrapers (per-run extras first, then built-ins), normalise, dedupe, cap, return.

        Per-run extras (e.g. CustomUrlScraper from user-pasted URLs) run BEFORE
        the always-on scrapers and their results land at the front of the list.
        That way the max_jobs cap further down can never silently truncate
        explicitly-requested URLs in favour of auto-discovered ones.

        ADR-064: when skip_builtin_adzuna is True the always-on (startup) Adzuna
        scraper is omitted for this run — the caller has supplied a per-run Adzuna
        scraper built from the run's search_criteria as an extra, and running the
        startup one too would re-search the senior startup titles.
        """
        raw_jobs: list[Any] = []
        builtins = list(self._scrapers)
        if skip_builtin_adzuna:
            builtins = [s for s in builtins
                        if type(s).__name__ != "ConcurrentAdzunaScraper"]
        all_scrapers = list(extra_scrapers or []) + builtins
        for scraper in all_scrapers:
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                future = pool.submit(scraper.scrape)
                try:
                    results = future.result(timeout=_SCRAPER_TIMEOUT_S)
                    logger.info("Scraper %s returned %d jobs", type(scraper).__name__, len(results))
                    raw_jobs.extend(results)
                except FuturesTimeoutError:
                    logger.warning(
                        "Scraper %s cancelled — exceeded %ds safety timeout",
                        type(scraper).__name__, _SCRAPER_TIMEOUT_S,
                    )
                except Exception as exc:
                    logger.error("Scraper %s failed — continuing: %s", type(scraper).__name__, exc)
            finally:
                pool.shutdown(wait=False)  # don't block — timed-out threads run to completion in bg

        postings = [self.normalize(job, workflow_id) for job in raw_jobs]
        postings = [p for p in postings if not self._is_excluded_title(p.title)]

        # ADR-065: per-profile years-of-experience cap. Drop postings whose stated
        # minimum experience exceeds the cap; keep postings with no detectable
        # experience (silent JDs are not penalized).
        if max_years_experience is not None:
            from app.services.experience_filter import exceeds_cap
            before = len(postings)
            postings = [p for p in postings
                        if not exceeds_cap(p.description, max_years_experience)]
            if before != len(postings):
                logger.info("Experience cap (<=%d yrs) dropped %d of %d postings",
                            max_years_experience, before - len(postings), before)

        postings = self.deduplicate(postings)

        if len(postings) > self._max_jobs:
            logger.info("Capping %d postings to max_jobs=%d", len(postings), self._max_jobs)
            postings = postings[: self._max_jobs]

        return postings

    def normalize(self, v1_job: Any, workflow_id: str) -> JobPosting:
        """Convert a v1 models.job.Job to a v2 JobPosting. Assigns a fresh UUID as job_id."""
        # JobSource — strip enum prefix if present (e.g. "JobSource.linkedin" → "linkedin")
        raw_source = str(getattr(v1_job, "source", "")).lower()
        raw_source = raw_source.split(".")[-1]  # handles both "linkedin" and "jobsource.linkedin"
        source = _SOURCE_MAP.get(raw_source, JobSource.MANUAL)

        # WorkMode — same prefix-stripping pattern
        raw_mode = str(getattr(v1_job, "work_mode", "") or "").lower()
        raw_mode = raw_mode.split(".")[-1]
        work_mode = _WORK_MODE_MAP.get(raw_mode, WorkMode.UNKNOWN)

        # Salary — v1 SalaryRange has min/max/currency
        v1_salary = getattr(v1_job, "salary", None)
        salary: SalaryInfo | None = None
        if v1_salary is not None:
            salary = SalaryInfo(
                min_amount=getattr(v1_salary, "min", None),
                max_amount=getattr(v1_salary, "max", None),
                currency=getattr(v1_salary, "currency", "USD"),
            )

        # Timestamps — v1 uses datetime objects; convert to ISO 8601 UTC strings
        found_at_raw = getattr(v1_job, "found_at", None)
        found_at = (
            found_at_raw.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            if found_at_raw else utcnow_iso()
        )

        posted_at_raw = getattr(v1_job, "posted_at", None)
        posted_at = (
            posted_at_raw.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            if posted_at_raw else None
        )

        return JobPosting(
            job_id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            url=getattr(v1_job, "url", ""),
            source=source,
            title=getattr(v1_job, "title", ""),
            company=getattr(v1_job, "company", ""),
            location=getattr(v1_job, "location", None),
            work_mode=work_mode,
            description=getattr(v1_job, "description", None),
            salary=salary,
            found_at=found_at,
            posted_at=posted_at,
        )

    def deduplicate(self, jobs: list[JobPosting]) -> list[JobPosting]:
        """Remove URL duplicates within the batch and against already-persisted jobs in the DB.

        ADR-057 note: this implicitly filters re-discoveries of excluded URLs
        too. Excluding a job leaves its row in `jobs` with `excluded=1`; a
        future scraper that surfaces the same URL hits `url_exists(url) ->
        True` here and is dropped before scoring. The cost saving from
        ADR-057 is realised at this exact line — no extra logic required.
        """
        seen_urls: set[str] = set()
        unique: list[JobPosting] = []
        for job in jobs:
            url = job.url
            if url in seen_urls:
                logger.debug("Dedup (batch): skipping duplicate URL %s", url)
                continue
            if self._repo.url_exists(url):
                logger.debug("Dedup (DB): URL already persisted — %s", url)
                continue
            seen_urls.add(url)
            unique.append(job)
        return unique

    @staticmethod
    def _is_excluded_title(title: str) -> bool:
        lower = title.lower()
        return any(kw in lower for kw in EXCLUDED_TITLE_KEYWORDS)
