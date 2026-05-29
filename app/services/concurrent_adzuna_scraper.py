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

# ADR-064: stopwords dropped when deriving title-relevance tokens from a profile's
# role list, so a per-run search keeps titles matching the searched roles.
_ROLE_STOPWORDS = frozenset({"of", "the", "and", "for", "to", "a", "an", "in", "on", "or"})

# ADR-065: curated seniority terms used at the LOCAL title gate. When a profile
# sets search.exclude_senior, these are appended to the per-run title-exclusion
# allowlist - substring match against the JOB TITLE only, so e.g. "Senior Security
# Analyst" and "Security Lead" are dropped. Includes polysemic terms (manager,
# lead, staff, head of) because at the title level they are reliable seniority
# signals.
SENIOR_TERMS = [
    "senior", "principal", "staff", "lead", "director", "vp", "vice president",
    "head of", "manager", "architect",
]

# ADR-065 calibration (2026-05-29): high-precision senior terms suitable for
# Adzuna's what_exclude. Adzuna matches what_exclude against TITLE AND
# DESCRIPTION, not title alone. The broader SENIOR_TERMS list above includes
# words like "manager", "lead", "staff", "head" that appear in countless
# entry-level posting descriptions ("reports to the security manager", "works
# with the team lead", "staff member of the SOC", "headquartered in NYC") and
# nuked legitimate matches at the source. This narrowed list keeps only the
# terms that are reliably senior wherever they appear in text.
SENIOR_TERMS_API_EXCLUDE = ["senior", "principal", "vp", "director"]


def relevance_tokens(roles: list[str]) -> list[str]:
    """Lowercase word tokens across the role phrases, minus stopwords/short tokens.

    e.g. ["Security Analyst", "SOC Analyst"] -> ["security", "analyst", "soc"].
    Used as the title-relevance allowlist for a per-run Adzuna search so non-senior
    roles are not filtered out by the senior default keyword list.
    """
    out: list[str] = []
    seen: set[str] = set()
    for role in roles or []:
        for raw in str(role).lower().split():
            tok = "".join(c for c in raw if c.isalnum())
            if len(tok) >= 3 and tok not in _ROLE_STOPWORDS and tok not in seen:
                seen.add(tok)
                out.append(tok)
    return out


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
    def make(cls, adzuna_config, titles: list[str], max_workers: int = _DEFAULT_WORKERS,
             relevant_keywords: list[str] | None = None,
             excluded_keywords: list[str] | None = None,
             what_exclude: list[str] | None = None):
        """Instantiate the v1 AdzunaScraper and wrap it. Returns None on failure.

        ADR-064: relevant_keywords/excluded_keywords flow through to the v1
        scraper's title-relevance gate so a per-run search can override the
        senior defaults (e.g. role-derived tokens for an entry-level profile).
        ADR-065: what_exclude is passed to Adzuna's query so senior terms are
        dropped at the source.
        """
        try:
            from scrapers.adzuna import AdzunaScraper
            v1 = AdzunaScraper(adzuna_config, titles,
                               relevant_keywords=relevant_keywords,
                               excluded_keywords=excluded_keywords,
                               what_exclude=what_exclude)
            return cls(v1, max_workers=max_workers)
        except Exception as exc:
            logger.warning("ConcurrentAdzunaScraper.make failed: %s", exc)
            return None
