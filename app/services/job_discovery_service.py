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
from app.services.url_canonicalizer import canonicalize_url
from models.filters import EXCLUDED_TITLE_KEYWORDS

logger = logging.getLogger(__name__)

# Hard cap: no single scraper may block the workflow longer than this.
# With ConcurrentAdzunaScraper (5 workers, ~7s/task), 62 calls ≈ 90s typical.
_SCRAPER_TIMEOUT_S = 180

_SOURCE_MAP: dict[str, JobSource] = {
    "linkedin": JobSource.LINKEDIN,
    "adzuna": JobSource.ADZUNA,
    # The v1 AdzunaScraper tags its results JobSource.INDEED ("Adzuna aggregates
    # Indeed among others"), so map "indeed" -> ADZUNA; without this they fell back
    # to MANUAL and were mislabeled in the UI.
    "indeed": JobSource.ADZUNA,
    "greenhouse": JobSource.GREENHOUSE,   # ADR-081
    "lever": JobSource.LEVER,             # ADR-081
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
        min_years_experience: int | None = None,
        max_posting_age_days: int | None = None,
        drop_dead_links: bool = False,
        exclude_senior: bool = False,
        user_id: str | None = None,
    ) -> list[JobPosting]:
        """Backward-compat wrapper. Returns postings only; discards stats.

        New callers that want the per-stage funnel counts should use
        `discover_with_stats(...)` and persist the second tuple element to
        the workflow state. See ADR-069 (or commit log dated 2026-05-29)
        for the diagnostic that motivated the explicit stats: silent
        dedup-against-DB drops were invisible at the workflow boundary.
        """
        postings, _stats = self.discover_with_stats(
            workflow_id, search_criteria,
            extra_scrapers=extra_scrapers,
            skip_builtin_adzuna=skip_builtin_adzuna,
            max_years_experience=max_years_experience,
            min_years_experience=min_years_experience,
            max_posting_age_days=max_posting_age_days,
            drop_dead_links=drop_dead_links,
            exclude_senior=exclude_senior,
            user_id=user_id,
        )
        return postings

    def discover_with_stats(
        self,
        workflow_id: str,
        search_criteria: dict,
        extra_scrapers: list[Any] | None = None,
        skip_builtin_adzuna: bool = False,
        max_years_experience: int | None = None,
        min_years_experience: int | None = None,
        max_posting_age_days: int | None = None,
        drop_dead_links: bool = False,
        exclude_senior: bool = False,
        restrict_to_profile_locations: bool = True,
        user_id: str | None = None,
    ) -> tuple[list[JobPosting], dict]:
        """Run all scrapers, normalise, filter, dedupe, cap. Returns
        (postings, stats). The stats dict carries per-stage funnel counts
        so the workflow can persist them and the UI can show what was
        dropped, where.

        Per-run extras (e.g. CustomUrlScraper from user-pasted URLs) run BEFORE
        the always-on scrapers and their results land at the front of the list.
        That way the max_jobs cap further down can never silently truncate
        explicitly-requested URLs in favour of auto-discovered ones.

        ADR-064: when skip_builtin_adzuna is True the always-on (startup) Adzuna
        scraper is omitted for this run.
        """
        per_scraper_stats: list[dict] = []
        raw_jobs: list[Any] = []
        builtins = list(self._scrapers)
        if skip_builtin_adzuna:
            builtins = [s for s in builtins
                        if type(s).__name__ != "ConcurrentAdzunaScraper"]
        all_scrapers = list(extra_scrapers or []) + builtins
        for scraper in all_scrapers:
            pool = ThreadPoolExecutor(max_workers=1)
            scraper_name = type(scraper).__name__
            try:
                future = pool.submit(scraper.scrape)
                try:
                    results = future.result(timeout=_SCRAPER_TIMEOUT_S)
                    logger.info("Scraper %s returned %d jobs", scraper_name, len(results))
                    raw_jobs.extend(results)
                    per_scraper_stats.append({"name": scraper_name, "raw": len(results)})
                except FuturesTimeoutError:
                    logger.warning(
                        "Scraper %s cancelled — exceeded %ds safety timeout",
                        scraper_name, _SCRAPER_TIMEOUT_S,
                    )
                    per_scraper_stats.append({"name": scraper_name, "raw": 0,
                                              "error": "timeout"})
                except Exception as exc:
                    logger.error("Scraper %s failed — continuing: %s", scraper_name, exc)
                    per_scraper_stats.append({"name": scraper_name, "raw": 0,
                                              "error": str(exc)[:120]})
            finally:
                pool.shutdown(wait=False)  # don't block — timed-out threads run to completion in bg

        scraper_raw_total = sum(s.get("raw", 0) for s in per_scraper_stats)
        postings = [self.normalize(job, workflow_id) for job in raw_jobs]

        before_title = len(postings)
        postings = [p for p in postings if not self._is_excluded_title(p.title)]
        title_filter_dropped = before_title - len(postings)

        # ADR-065: per-profile years-of-experience window. Drop postings outside
        # [min, max]; keep postings with no detectable experience (silent JDs are
        # not penalized). max compares the JD's lowest bar, min its highest bar.
        experience_filter_dropped = 0
        if max_years_experience is not None or min_years_experience is not None:
            from app.services.experience_filter import exceeds_cap, below_floor
            before_exp = len(postings)
            if max_years_experience is not None:
                postings = [p for p in postings
                            if not exceeds_cap(p.description, max_years_experience)]
            if min_years_experience is not None:
                postings = [p for p in postings
                            if not below_floor(p.description, min_years_experience)]
            experience_filter_dropped = before_exp - len(postings)
            if experience_filter_dropped > 0:
                logger.info("Experience window (min=%s max=%s) dropped %d of %d postings",
                            min_years_experience, max_years_experience,
                            experience_filter_dropped, before_exp)

        # ADR-065 extension (BUG-010): title-based seniority drop. The body-based
        # experience filter above misses aggregator snippets truncated past the
        # "5+ years" line; the TITLE ("... - Mid", "Analyst II", "Tier 3") is never
        # truncated, so when an entry profile sets exclude_senior we also drop
        # above-entry TITLES across ALL sources (Adzuna + ATS).
        seniority_title_dropped = 0
        if exclude_senior:
            from app.services.seniority_filter import title_is_above_entry
            before_sen = len(postings)
            postings = [p for p in postings if not title_is_above_entry(p.title)]
            seniority_title_dropped = before_sen - len(postings)
            if seniority_title_dropped > 0:
                logger.info("Seniority title filter dropped %d of %d postings (exclude_senior)",
                            seniority_title_dropped, before_sen)

        # ADR-080: per-profile max posting age. Drop postings strictly older than
        # the cap; keep postings with no parseable posted_at (unknown age is not
        # penalized, mirroring the experience filter). Deterministic, no fetch.
        age_filter_dropped = 0
        if max_posting_age_days:
            from app.services.posting_age_filter import is_older_than
            before_age = len(postings)
            postings = [p for p in postings
                        if not is_older_than(p.posted_at, max_posting_age_days)]
            age_filter_dropped = before_age - len(postings)
            if age_filter_dropped > 0:
                logger.info("Posting-age cap (max_days=%s) dropped %d of %d postings",
                            max_posting_age_days, age_filter_dropped, before_age)

        # ADR-103: profile-derived location filter. The ATS-direct scrapers return
        # a company's entire GLOBAL board with no location gate, so out-of-country
        # postings leak into an in-country profile. Derive the allowed country set
        # from THIS profile's own search.locations and drop postings confidently
        # resolved to a country it did not ask for. Uniform across all sources;
        # keep-on-ambiguity (never-lose-the-run); empty allowed set = no-op.
        location_dropped = 0
        location_samples: list[dict] = []
        if restrict_to_profile_locations:
            from app.services.location_filter import (
                derive_allowed_countries,
                filter_by_location,
            )
            allowed = derive_allowed_countries(search_criteria.get("locations"))
            if allowed:
                before_loc = len(postings)
                postings, location_dropped, location_samples = filter_by_location(
                    postings, allowed
                )
                if location_dropped > 0:
                    logger.info(
                        "Location filter (allowed=%s) dropped %d of %d postings",
                        sorted(allowed), location_dropped, before_loc,
                    )

        before_dedup = len(postings)
        postings, dedup_stats = self._dedup_with_stats(postings, user_id=user_id)
        dedup_total_dropped = before_dedup - len(postings)

        # ADR-095: opt-in best-effort dead-link filter. Drops postings whose apply
        # link is VERIFIABLY dead (404/410 or a closed-job marker); keeps anything
        # ambiguous (timeout/429/DNS) so a transient fault never loses a job. Network
        # I/O, so it runs after dedup (fewer URLs) and only when the profile opts in.
        dead_link_dropped = 0
        dead_link_samples: list[dict] = []
        if drop_dead_links and postings:
            from app.services.dead_link_filter import filter_dead_links
            before_dead = len(postings)
            postings, dead_link_dropped, dead_link_samples = filter_dead_links(postings)
            if dead_link_dropped:
                logger.info("Dead-link filter dropped %d of %d postings",
                            dead_link_dropped, before_dead)

        # ADR-102: source-fair round-robin BEFORE any cap, so the three
        # order-sensitive truncations downstream (this _max_jobs cap, the
        # discover_jobs node cap, the score_jobs scoring cap) no longer starve a
        # later-appended source (Greenhouse/Lever) in favor of the always-first
        # Adzuna. Reorders only - never drops - so the funnel counts are unchanged.
        from app.services.source_interleave import interleave_by_source, source_mix
        postings = interleave_by_source(postings)

        before_cap = len(postings)
        max_jobs_truncated = 0
        if len(postings) > self._max_jobs:
            logger.info("Capping %d postings to max_jobs=%d", len(postings), self._max_jobs)
            max_jobs_truncated = len(postings) - self._max_jobs
            postings = postings[: self._max_jobs]

        stats = {
            "per_scraper":               per_scraper_stats,
            "scraper_raw_total":         scraper_raw_total,
            "title_filter_dropped":      title_filter_dropped,
            "experience_filter_dropped": experience_filter_dropped,
            "seniority_title_dropped":   seniority_title_dropped,
            "age_filter_dropped":        age_filter_dropped,
            "location_dropped":          location_dropped,
            "location_samples":          location_samples,
            "dedup_total_dropped":       dedup_total_dropped,
            "dedup_batch_dropped":       dedup_stats["batch"],
            "dedup_excluded_dropped":    dedup_stats["excluded"],
            "dedup_user_scored_dropped": dedup_stats["user_scored"],
            "dead_link_dropped":         dead_link_dropped,
            "dead_link_samples":         dead_link_samples,
            "max_jobs_truncated":        max_jobs_truncated,
            "returned":                  len(postings),
            # ADR-102: per-source counts of what survived to `returned`, so the
            # source mix (and any residual cap bias) is observable on Workflow Detail.
            "source_mix":                source_mix(postings),
            "user_id":                   user_id,
        }
        logger.info(
            "discover funnel: scraped=%d -> title=%d -> exp=%d -> "
            "dedup(batch=%d,excl=%d,user=%d) -> cap=%d -> returned=%d",
            scraper_raw_total,
            scraper_raw_total - title_filter_dropped,
            scraper_raw_total - title_filter_dropped - experience_filter_dropped,
            dedup_stats["batch"], dedup_stats["excluded"], dedup_stats["user_scored"],
            before_cap, len(postings),
        )
        return postings, stats

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

        # Canonicalize the URL before it leaves normalize. Some sources
        # (Adzuna in particular) rotate a session token in the query
        # string per fetch; canonicalize_url strips those so the URL is
        # stable across re-fetches and dedup actually catches duplicates.
        # See app/services/url_canonicalizer.py for the full rationale.
        return JobPosting(
            job_id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            url=canonicalize_url(getattr(v1_job, "url", "")),
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

    def deduplicate(self, jobs: list[JobPosting],
                    user_id: str | None = None) -> list[JobPosting]:
        """Backward-compat wrapper around `_dedup_with_stats`. Returns the
        deduped list only; stats are dropped. Accepts `user_id` so callers
        that want per-user dedup (cost saver: don't re-score URLs this user
        already scored) get it.

        Three dedup stages, in order:
          1. Batch dedup: drop URL duplicates within this batch.
          2. Global excluded dedup: drop URLs flagged excluded in `jobs`
             (ADR-057 "stay excluded" - excluded by ANY user stays
             excluded for everyone).
          3. Per-user already-scored dedup: drop URLs this user has
             already scored in a prior run. Different users score
             independently. Skipped if user_id is None or empty.

        The old "drop any URL already persisted to `jobs`" behavior was
        retired 2026-05-29 - it accidentally collapsed re-discovery for
        new profiles and repeat runs (the cyber profile saw 1 job for
        three runs in a row because Adzuna kept returning the same set
        and dedup dropped everything as already-persisted).
        """
        unique, _stats = self._dedup_with_stats(jobs, user_id=user_id)
        return unique

    def _dedup_with_stats(self, jobs: list[JobPosting],
                          user_id: str | None) -> tuple[list[JobPosting], dict]:
        seen_urls: set[str] = set()
        unique: list[JobPosting] = []
        batch_dropped = 0
        excluded_dropped = 0
        user_scored_dropped = 0
        for job in jobs:
            url = job.url
            if url in seen_urls:
                batch_dropped += 1
                logger.debug("Dedup (batch): skipping duplicate URL %s", url)
                continue
            if self._repo.url_excluded(url):
                excluded_dropped += 1
                logger.debug("Dedup (excluded): URL is flagged excluded - %s", url)
                continue
            if user_id and self._repo.url_scored_by_user(url, user_id):
                user_scored_dropped += 1
                logger.debug("Dedup (user-scored): URL already scored by user %s - %s",
                             user_id, url)
                continue
            seen_urls.add(url)
            unique.append(job)
        return unique, {
            "batch":       batch_dropped,
            "excluded":    excluded_dropped,
            "user_scored": user_scored_dropped,
        }

    @staticmethod
    def _is_excluded_title(title: str) -> bool:
        lower = title.lower()
        return any(kw in lower for kw in EXCLUDED_TITLE_KEYWORDS)
