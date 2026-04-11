# scrapers/adzuna.py
# ─────────────────────────────────────────────────────────────────────────────
# Adzuna API scraper — replaces both Indeed and Glassdoor scrapers.
# Adzuna aggregates jobs from hundreds of sources and returns clean JSON.
# Requires ADZUNA_APP_ID and ADZUNA_APP_KEY in your .env file.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import os
from datetime import datetime
from urllib.parse import urlencode

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from models.job import Job, JobSource
from models.config_schema import AdzunaConfig
from models.filters import RELEVANT_TITLE_KEYWORDS, EXCLUDED_TITLE_KEYWORDS
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Adzuna REST API base URL — country code is inserted per call
ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"


class AdzunaScraper(BaseScraper):
    """
    Fetches job listings from the Adzuna API.

    For each keyword in config, makes one API call and converts the results
    into Job objects. Adzuna returns structured JSON — no HTML parsing needed.

    Authentication:
      Reads ADZUNA_APP_ID and ADZUNA_APP_KEY from environment variables.
      These must be set in your .env file before running.

    Rate limits:
      Free tier allows 100 calls/day.
      Budget = (len(titles) × len(locations)) + len(remote_keywords).
    """

    def __init__(self, config: AdzunaConfig, titles: list[str]) -> None:
        """
        Args:
            config: AdzunaConfig from config.yaml (locations, radius, remote_keywords, etc.)
            titles: Job titles to search for — comes from search.titles in AppConfig,
                    the single source of truth for what roles the user is targeting.
        """
        super().__init__("adzuna")
        self.config = config
        self.titles = titles

        # Read credentials from environment — set in .env
        self.app_id = os.getenv("ADZUNA_APP_ID")
        self.app_key = os.getenv("ADZUNA_APP_KEY")

        if not self.app_id or not self.app_key:
            raise EnvironmentError(
                "ADZUNA_APP_ID and ADZUNA_APP_KEY must be set in your .env file. "
                "Get them from https://developer.adzuna.com"
            )

    def scrape(self) -> list[Job]:
        """
        Fetches jobs for all configured keywords and returns deduplicated results.

        Returns:
            List of Job objects, deduplicated by URL.
        """
        if not self.config.enabled:
            logger.info("Adzuna scraper is disabled in config")
            return []

        seen_urls: set[str] = set()
        jobs: list[Job] = []

        # Local search — one call per title × location combination
        # Titles come from search.titles (AppConfig) — the single source of truth
        for location in self.config.locations:
            for keyword in self.titles:
                try:
                    new_jobs = self._fetch_jobs(keyword, location=location)
                    for job in new_jobs:
                        if job.url not in seen_urls:
                            seen_urls.add(job.url)
                            jobs.append(job)
                except Exception as e:
                    logger.warning(
                        "Adzuna fetch failed for keyword '%s' in '%s': %s",
                        keyword, location, e,
                    )

        # Remote search — no location filter, appends "remote" to each keyword
        for keyword in self.config.remote_keywords:
            try:
                new_jobs = self._fetch_jobs(f"{keyword} remote", location="")
                for job in new_jobs:
                    if job.url not in seen_urls:
                        seen_urls.add(job.url)
                        jobs.append(job)
            except Exception as e:
                logger.warning("Adzuna remote fetch failed for keyword '%s': %s", keyword, e)

        self.log_result(jobs)
        return jobs

    # ─── Private helpers ──────────────────────────────────────────────────────

    def _build_url(self, keyword: str, location: str = "") -> str:
        """
        Builds the Adzuna API search URL for a given keyword.

        Args:
            keyword : Search term, e.g. 'software engineer'
            location: City/state to scope the search. Empty string = no location filter (US-wide).

        Returns:
            Full API URL with query parameters.
        """
        base = ADZUNA_BASE.format(country=self.config.country)

        params: dict = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": keyword,
            "results_per_page": self.config.results_per_page,
            "sort_by": "date",
            "full_time": 1,
            "content-type": "application/json",
        }

        if location:
            params["where"] = location
            params["distance"] = self.config.radius_km

        return f"{base}?{urlencode(params)}"

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=8),
        stop=stop_after_attempt(3),
    )
    def _fetch_jobs(self, keyword: str, location: str = "") -> list[Job]:
        """
        Makes one Adzuna API call for the given keyword and parses the results.

        Args:
            keyword : Search term to pass to the API.
            location: Optional location filter. Empty = US-wide / remote search.

        Returns:
            List of Job objects from the API response.
        """
        url = self._build_url(keyword, location=location)
        logger.debug("Calling Adzuna API for keyword '%s'", keyword)

        with httpx.Client(timeout=15, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()

            data = response.json()
            results = data.get("results", [])

            logger.debug(
                "Adzuna returned %d results for keyword '%s'",
                len(results),
                keyword,
            )

            jobs: list[Job] = []
            for item in results:
                try:
                    job = self._parse_result(item)
                    if job:
                        # Follow Adzuna's tracking redirect to get the real posting URL
                        job.url = self._resolve_url(client, job.url)
                        jobs.append(job)
                except Exception as e:
                    logger.warning("Failed to parse Adzuna result: %s", e)

        return jobs

    @staticmethod
    def _resolve_url(client: httpx.Client, redirect_url: str) -> str:
        """
        Follows Adzuna's tracking redirect to get the actual job posting URL.
        Uses a HEAD request to avoid downloading the full page.
        Falls back to the original redirect URL on any error.
        """
        try:
            resp = client.head(redirect_url, timeout=5)
            final_url = str(resp.url)
            if final_url != redirect_url:
                logger.debug("Resolved %s → %s", redirect_url, final_url)
            return final_url
        except Exception as e:
            logger.debug("URL resolution failed for %s: %s", redirect_url, e)
            return redirect_url

    def _is_relevant_title(self, title: str) -> bool:
        """
        Returns True if the title matches a relevant keyword and does not
        match any excluded keyword. Filters out noise before Claude scoring.
        """
        title_lower = title.lower()
        if not any(kw in title_lower for kw in RELEVANT_TITLE_KEYWORDS):
            return False
        if any(kw in title_lower for kw in EXCLUDED_TITLE_KEYWORDS):
            logger.debug("Excluding title (matched exclusion list): %s", title)
            return False
        return True

    def _parse_result(self, item: dict) -> Job | None:
        """
        Converts a single Adzuna API result dict into a Job object.

        Adzuna result fields we use:
          - title           : job title string
          - company         : nested dict with display_name
          - location        : nested dict with display_name
          - description     : plain text job description snippet
          - redirect_url    : the URL to the actual job posting
          - salary_min      : minimum salary if provided
          - salary_max      : maximum salary if provided
          - created         : ISO 8601 posted date string
          - contract_time   : 'full_time' or 'part_time'

        Args:
            item: A single result dict from the Adzuna API response.

        Returns:
            A Job object, or None if required fields are missing.
        """
        # Required fields — skip if missing
        title = item.get("title")
        url = item.get("redirect_url")
        if not title or not url:
            return None

        # Skip titles that don't match any relevant keyword
        # This filters out plumbers, interns, accountants etc before scoring
        if not self._is_relevant_title(title):
            logger.debug("Skipping irrelevant title: %s", title)
            return None

        # Company name is nested
        company = item.get("company", {}).get("display_name") or "Unknown"

        # Location is nested
        location = (
            item.get("location", {}).get("display_name") or self.config.location or None
        )

        # Description is a plain text snippet — not the full posting
        description = item.get("description")

        # Salary — both fields are optional
        salary_min = item.get("salary_min")
        salary_max = item.get("salary_max")
        salary = None
        if salary_min or salary_max:
            from models.job import SalaryRange

            salary = SalaryRange(
                min=int(salary_min) if salary_min else None,
                max=int(salary_max) if salary_max else None,
                currency="USD",
            )

        # Posted date — Adzuna returns ISO 8601 e.g. '2024-03-15T10:30:00Z'
        posted_at = None
        created = item.get("created")
        if created:
            try:
                posted_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                logger.debug("Could not parse Adzuna created date: %s", created)

        # Work mode — Adzuna does not expose this directly, infer from title/description
        work_mode = self._infer_work_mode(title, description or "")

        return Job(
            url=url,
            source=JobSource.INDEED,  # Adzuna aggregates Indeed among others
            title=title,
            company=company,
            location=location,
            work_mode=work_mode,
            description=description,
            salary=salary,
            posted_at=posted_at,
        )

    @staticmethod
    def _infer_work_mode(title: str, description: str) -> str | None:
        """
        Infers the work mode from job title and description text.
        Adzuna does not provide a structured work_mode field so we scan the text.

        Returns 'remote', 'hybrid', 'onsite', or None if unclear.
        """
        text = (title + " " + description).lower()

        if "remote" in text:
            return "remote"
        if "hybrid" in text:
            return "hybrid"
        if "onsite" in text or "on-site" in text or "in office" in text:
            return "onsite"
        return None
