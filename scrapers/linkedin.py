# scrapers/linkedin.py
# ─────────────────────────────────────────────────────────────────────────────
# LinkedIn scraper — reads URLs from inbox/linkedin.txt, one per line.
# You paste LinkedIn job URLs manually into that file as you browse.
# This scraper fetches each URL, extracts the job description, and returns
# a list of Job objects ready for Claude to score.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from models.job import Job, JobSource
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# HTTP headers that mimic a real browser — reduces chance of being blocked
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class LinkedInScraper(BaseScraper):
    """
    Reads LinkedIn job URLs from a local inbox file and fetches each posting.

    Workflow:
      1. Read inbox_file line by line, skipping blanks and comments (#)
      2. Fetch each URL with httpx
      3. Parse title, company, location, and description from the HTML
      4. Return a list of Job objects
      5. Clear the inbox file so URLs are not processed twice

    LinkedIn does not provide an RSS feed, so this manual approach is the
    most reliable way to handle it without requiring a browser automation tool.
    """

    def __init__(self, inbox_file: str) -> None:
        """
        Args:
            inbox_file: Path to the text file containing LinkedIn URLs,
                        one per line. Relative to the project root.
        """
        super().__init__("linkedin")
        self.inbox_path = Path(inbox_file)

    def scrape(self) -> list[Job]:
        """
        Reads URLs from the inbox file and fetches each job posting.

        Returns:
            List of Job objects, one per valid URL in the inbox.
        """
        urls = self._read_inbox()
        if not urls:
            logger.info("LinkedIn inbox is empty — nothing to scrape")
            return []

        jobs: list[Job] = []
        for url in urls:
            try:
                job = self._fetch_job(url)
                if job:
                    jobs.append(job)
            except Exception as e:
                # Log and continue — one bad URL should not stop the whole run
                logger.warning("Failed to fetch LinkedIn URL %s: %s", url, e)

        # Clear the inbox so these URLs are not processed again next run
        self._clear_inbox()

        self.log_result(jobs)
        return jobs

    # ─── Private helpers ──────────────────────────────────────────────────────

    def _read_inbox(self) -> list[str]:
        """
        Reads the inbox file and returns a list of non-empty, non-comment lines.
        Creates the file if it does not exist.
        """
        if not self.inbox_path.exists():
            # Create the file so the user knows where to paste URLs
            self.inbox_path.parent.mkdir(parents=True, exist_ok=True)
            self.inbox_path.write_text("# Paste LinkedIn job URLs here, one per line\n")
            return []

        lines = self.inbox_path.read_text(encoding="utf-8").splitlines()
        return [
            line.strip()
            for line in lines
            if line.strip() and not line.strip().startswith("#")
        ]

    def _clear_inbox(self) -> None:
        """
        Overwrites the inbox file with just the header comment.
        Called after all URLs have been processed.
        """
        self.inbox_path.write_text("# Paste LinkedIn job URLs here, one per line\n")
        logger.debug("LinkedIn inbox cleared")

    # Permitted hostnames for LinkedIn job URLs.
    # Any URL in the inbox that does not match is skipped without a network request.
    _ALLOWED_HOSTS = {"www.linkedin.com", "linkedin.com"}

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=8),
        stop=stop_after_attempt(3),
    )
    def _fetch_job(self, url: str) -> Job | None:
        """
        Fetches a single LinkedIn job URL and parses it into a Job object.

        Validates that the URL belongs to linkedin.com before making any
        network request — prevents fetching arbitrary URLs placed in the inbox.

        Args:
            url: The LinkedIn job posting URL.

        Returns:
            A Job object if parsing succeeds, None if the page is unrecognisable.
        """
        parsed = urlparse(url)
        if parsed.scheme not in {"https"} or parsed.netloc not in self._ALLOWED_HOSTS:
            logger.warning("Skipping non-LinkedIn URL (not trusted): %s", url)
            return None

        logger.debug("Fetching LinkedIn URL: %s", url)

        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=15) as client:
            response = client.get(url)
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # LinkedIn's HTML structure — these selectors may need updating
        # if LinkedIn changes their markup
        title   = self._text(soup, "h1.top-card-layout__title")
        company = self._text(soup, "a.topcard__org-name-link") or self._text(soup, "span.topcard__flavor")
        location = self._text(soup, "span.topcard__flavor--bullet")
        description = self._text(soup, "div.show-more-less-html__markup")

        if not title or not company:
            logger.warning("Could not parse title/company from LinkedIn URL: %s", url)
            return None

        return Job(
            url=url,
            source=JobSource.LINKEDIN,
            title=title,
            company=company,
            location=location,
            description=description,
        )

    @staticmethod
    def _text(soup: BeautifulSoup, selector: str) -> str | None:
        """
        Finds the first element matching selector and returns its stripped text.
        Returns None if the element is not found.
        """
        el = soup.select_one(selector)
        return el.get_text(strip=True) if el else None
