# scrapers/ladders.py
# ─────────────────────────────────────────────────────────────────────────────
# Ladders scraper — fetches job listings from Ladders.com.
# Ladders focuses on $100k+ roles, which aligns with our salary target.
# Uses httpx to fetch search result pages and BeautifulSoup to parse them.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from models.job import Job, JobSource
from models.config_schema import LaddersConfig
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Ladders job search URL
LADDERS_SEARCH_BASE = "https://www.theladders.com/jobs/searchjobs"

# Browser-like headers to reduce chance of being blocked
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class LaddersScraper(BaseScraper):
    """
    Fetches job listings from Ladders.com for each configured keyword.

    Ladders does not provide an RSS feed, so we fetch their search results
    page and parse the HTML. This is more fragile than RSS — if Ladders
    changes their markup, the selectors below may need updating.

    Note: Ladders may require a free account login to view full job details.
    This scraper captures what is available on the public search results page.
    Full descriptions are fetched by the extraction agent if needed.
    """

    def __init__(self, config: LaddersConfig) -> None:
        """
        Args:
            config: LaddersConfig from config.yaml — keywords.
        """
        super().__init__("ladders")
        self.config = config

    def scrape(self) -> list[Job]:
        """
        Fetches jobs for all configured keywords and returns deduplicated results.

        Returns:
            List of Job objects, deduplicated by URL.
        """
        if not self.config.enabled:
            logger.info("Ladders scraper is disabled in config")
            return []

        seen_urls: set[str] = set()
        jobs: list[Job] = []

        for keyword in self.config.keywords:
            try:
                new_jobs = self._fetch_listings(keyword)
                for job in new_jobs:
                    if job.url not in seen_urls:
                        seen_urls.add(job.url)
                        jobs.append(job)
            except Exception as e:
                logger.warning("Ladders scrape failed for keyword '%s': %s", keyword, e)

        self.log_result(jobs)
        return jobs

    # ─── Private helpers ──────────────────────────────────────────────────────

    def _search_url(self, keyword: str) -> str:
        """
        Builds the Ladders search URL for a given keyword.

        Args:
            keyword: Search term, e.g. "engineering manager"

        Returns:
            Full search URL string.
        """
        params = {
            "keywords": keyword,
            "sort": "date",  # most recent first
        }
        return f"{LADDERS_SEARCH_BASE}?{urlencode(params)}"

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=8),
        stop=stop_after_attempt(3),
    )
    def _fetch_listings(self, keyword: str) -> list[Job]:
        """
        Fetches the Ladders search results page and parses job cards.

        Args:
            keyword: Search term to use in the URL.

        Returns:
            List of Job objects parsed from the search results page.
        """
        url = self._search_url(keyword)
        logger.debug("Fetching Ladders search page: %s", url)

        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=15) as client:
            response = client.get(url)
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        job_cards = soup.select("li.job-card, div[data-testid='job-card'], article.job")

        if not job_cards:
            logger.warning(
                "No job cards found on Ladders search page for '%s' — "
                "markup may have changed, check selectors in ladders.py",
                keyword,
            )
            return []

        jobs: list[Job] = []
        for card in job_cards:
            try:
                job = self._parse_card(card)
                if job:
                    jobs.append(job)
            except Exception as e:
                logger.warning("Failed to parse Ladders job card: %s", e)

        logger.debug("Ladders returned %d cards for keyword '%s'", len(jobs), keyword)
        return jobs

    def _parse_card(self, card: BeautifulSoup) -> Job | None:
        """
        Parses a single Ladders job card HTML element into a Job object.

        Args:
            card: A BeautifulSoup element representing one job listing card.

        Returns:
            A Job object, or None if required fields cannot be extracted.
        """
        # These selectors are best-effort — Ladders may change their markup
        title_el = card.select_one("a.job-title, h2.title, [data-testid='job-title']")
        company_el = card.select_one("span.company, [data-testid='company-name']")
        location_el = card.select_one("span.location, [data-testid='job-location']")
        link_el = card.select_one("a[href]")

        title = title_el.get_text(strip=True) if title_el else None
        company = company_el.get_text(strip=True) if company_el else None
        location = location_el.get_text(strip=True) if location_el else None

        # Build absolute URL from the link href
        href = link_el["href"] if link_el else None
        if href and not href.startswith("http"):
            href = f"https://www.theladders.com{href}"

        if not title or not href:
            return None

        return Job(
            url=href,
            source=JobSource.LADDERS,
            title=title,
            company=company or "Unknown",
            location=location,
        )
