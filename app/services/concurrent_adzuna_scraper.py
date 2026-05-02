"""ConcurrentAdzunaScraper — v2 subclass that parallelises AdzunaScraper._fetch_jobs.

Two optimisations over the v1 scraper:
  1. Concurrent calls — fans 62 serial title×location calls out across a thread pool.
  2. Skip URL resolution — _resolve_url is overridden to return the redirect URL
     as-is, eliminating 620 extra HEAD requests (up to 5s each). Adzuna's redirect
     URLs still open the real job posting; we just don't pre-resolve them.

v1 code is NOT modified — we subclass only.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

_DEFAULT_WORKERS = 5


class ConcurrentAdzunaScraper:
    """Wraps AdzunaScraper with concurrent fetching and no URL resolution overhead."""

    def __init__(self, v1_scraper, max_workers: int = _DEFAULT_WORKERS) -> None:
        self._scraper = v1_scraper
        self._max_workers = max_workers
        # Patch _resolve_url on the instance so _fetch_jobs skips HEAD requests
        self._scraper._resolve_url = lambda client, url: url

    def scrape(self):
        s = self._scraper
        if not s.config.enabled:
            logger.info("Adzuna scraper is disabled in config")
            return []

        tasks: list[tuple[str, str]] = []
        for location in s.config.locations:
            for keyword in s.titles:
                tasks.append((keyword, location))
        for keyword in s.config.remote_keywords:
            tasks.append((f"{keyword} remote", ""))

        logger.info(
            "ConcurrentAdzunaScraper: %d tasks, %d workers (URL resolution skipped)",
            len(tasks), self._max_workers,
        )

        seen_urls: set[str] = set()
        jobs = []

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(s._fetch_jobs, keyword, location): (keyword, location)
                for keyword, location in tasks
            }
            for future in as_completed(futures):
                keyword, location = futures[future]
                try:
                    new_jobs = future.result()
                    for job in new_jobs:
                        if job.url not in seen_urls:
                            seen_urls.add(job.url)
                            jobs.append(job)
                except Exception as exc:
                    logger.warning(
                        "Adzuna fetch failed for '%s' / '%s': %s",
                        keyword, location, exc,
                    )

        s.log_result(jobs)
        return jobs

    @classmethod
    def make(cls, adzuna_config, titles: list[str], max_workers: int = _DEFAULT_WORKERS):
        """Instantiate the v1 AdzunaScraper and wrap it. Returns None on failure."""
        try:
            from scrapers.adzuna import AdzunaScraper
            v1 = AdzunaScraper(adzuna_config, titles)
            return cls(v1, max_workers=max_workers)
        except Exception as exc:
            logger.warning("ConcurrentAdzunaScraper.make failed: %s", exc)
            return None
