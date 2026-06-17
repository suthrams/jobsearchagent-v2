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
from concurrent.futures import TimeoutError as FuturesTimeoutError

from app.services.rate_limiter import get_adzuna_limiter

logger = logging.getLogger(__name__)

_DEFAULT_WORKERS = 5

# ADR-107: default per-minute call budget, kept under Adzuna's 25/min cap with margin.
_DEFAULT_MAX_CALLS_PER_MINUTE = 20

# ADR-108: default cap on Adzuna calls per run. ~50 calls at 20/min ~= 150s (inside the
# discovery timeout below) and over-feeds the 50-job funnel. 0 = uncapped.
_DEFAULT_MAX_CALLS_PER_RUN = 50

# ADR-108: wall-clock budget for one Adzuna scrape, set BELOW JobDiscoveryService's
# _SCRAPER_TIMEOUT_S (180s) so scrape() returns PARTIAL results on its own deadline instead
# of being hard-killed by the outer timeout (which would discard everything collected).
_SCRAPE_TIME_BUDGET_S = 150.0


def _interleave_tasks(titles: list[str], locations: list[str]) -> list[tuple[str, str]]:
    """Latin-square interleave of the title x location grid (ADR-108).

    Ordered so a truncated prefix samples across MANY titles AND MANY locations, rather
    than exhausting all titles for the first location (which a naive nested loop does).
    Each diagonal pass pairs ``location = (title + shift) % L``, so the first L*T pairs
    cover every (title, location) once and the first ~T pairs already span all titles and
    cycle all locations. Returns [] when there are no locations or no titles.
    """
    n_titles, n_locs = len(titles), len(locations)
    if n_titles == 0 or n_locs == 0:
        return []
    out: list[tuple[str, str]] = []
    for shift in range(n_locs):
        for ti in range(n_titles):
            li = (ti + shift) % n_locs
            out.append((titles[ti], locations[li]))
    return out


def _extract_429_retry_after(exc: BaseException) -> float | None:
    """If ``exc`` (possibly a tenacity RetryError wrapper) is an HTTP 429, return its
    ``Retry-After`` seconds (or a default), else None. Best-effort + never raises."""
    seen: set[int] = set()
    stack = [exc]
    while stack:
        e = stack.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        # Unwrap a tenacity RetryError to its last underlying attempt.
        last = getattr(e, "last_attempt", None)
        if last is not None:
            try:
                stack.append(last.exception())
            except Exception:  # noqa: BLE001
                pass
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
        if status == 429:
            try:
                ra = resp.headers.get("retry-after")
                return float(ra) if ra is not None else 30.0
            except (TypeError, ValueError, AttributeError):
                return 30.0
        stack.append(getattr(e, "__cause__", None))
        stack.append(getattr(e, "__context__", None))
    return None

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

    def __init__(self, v1_scraper, max_workers: int = _DEFAULT_WORKERS,
                 max_calls_per_minute: int = _DEFAULT_MAX_CALLS_PER_MINUTE,
                 max_calls_per_run: int = _DEFAULT_MAX_CALLS_PER_RUN,
                 time_budget_s: float = _SCRAPE_TIME_BUDGET_S) -> None:
        self._scraper = v1_scraper
        self._max_workers = max_workers
        # ADR-107: process-global limiter shared by every Adzuna scraper instance, so the
        # provider's per-minute cap is respected even across concurrent runs. None = off.
        self._limiter = get_adzuna_limiter(max_calls_per_minute)
        # ADR-108: bound calls per run + a wall-clock budget for partial results.
        self._max_calls_per_run = max_calls_per_run
        self._time_budget_s = time_budget_s
        # Patch _resolve_url on the instance so _fetch_jobs skips HEAD requests
        self._scraper._resolve_url = lambda client, url: url

    def scrape(self):
        s = self._scraper
        if not s.config.enabled:
            logger.info("Adzuna scraper is disabled in config")
            return []

        # ADR-108: diagonal-interleave the local grid so a truncated harvest samples
        # across many titles AND locations; remote tasks (US-wide, no location) appended.
        tasks: list[tuple[str, str]] = _interleave_tasks(s.titles, list(s.config.locations))
        for keyword in s.config.remote_keywords:
            tasks.append((f"{keyword} remote", ""))

        # ADR-108: cap calls per run (one call per task) so an unbounded role/location grid
        # cannot blow the per-minute/daily quotas or the discovery timeout. Log the drop.
        total_tasks = len(tasks)
        cap = self._max_calls_per_run
        if cap and cap > 0 and total_tasks > cap:
            logger.warning(
                "ConcurrentAdzunaScraper: capping %d tasks to %d calls/run (ADR-108); "
                "%d title/location combinations dropped this run.",
                total_tasks, cap, total_tasks - cap,
            )
            tasks = tasks[:cap]

        logger.info(
            "ConcurrentAdzunaScraper: %d tasks (of %d), %d workers, budget %.0fs "
            "(URL resolution skipped)",
            len(tasks), total_tasks, self._max_workers, self._time_budget_s,
        )

        seen_urls: set[str] = set()
        jobs = []

        def _throttled_fetch(keyword: str, location: str):
            # ADR-107: pace the actual call START to the shared per-minute budget. Run
            # in the worker thread so the 5 workers' starts are serialized by the limiter
            # while their HTTP response waits still overlap.
            if self._limiter is not None:
                self._limiter.acquire()
            return s._fetch_jobs(keyword, location)

        # ADR-108: do NOT use a `with` block — its __exit__ waits for ALL tasks. We bound
        # the collection to a time budget and cancel the rest, returning partial results.
        executor = ThreadPoolExecutor(max_workers=self._max_workers)
        try:
            futures = {
                executor.submit(_throttled_fetch, keyword, location): (keyword, location)
                for keyword, location in tasks
            }
            done = 0
            try:
                for future in as_completed(futures, timeout=self._time_budget_s):
                    keyword, location = futures[future]
                    done += 1
                    try:
                        new_jobs = future.result()
                        for job in new_jobs:
                            if job.url not in seen_urls:
                                seen_urls.add(job.url)
                                jobs.append(job)
                    except Exception as exc:
                        # ADR-107: if Adzuna rate-limited us, back the shared limiter off
                        # so the remaining tasks slow down (best-effort honor-Retry-After).
                        if self._limiter is not None:
                            retry_after = _extract_429_retry_after(exc)
                            if retry_after is not None:
                                self._limiter.penalize(retry_after)
                                logger.warning(
                                    "Adzuna 429 for '%s' / '%s'; backing off %.0fs",
                                    keyword, location, retry_after,
                                )
                        logger.warning(
                            "Adzuna fetch failed for '%s' / '%s': %s",
                            keyword, location, exc,
                        )
            except FuturesTimeoutError:
                # ADR-108: budget hit before all tasks finished — keep what we have.
                logger.warning(
                    "ConcurrentAdzunaScraper: %.0fs budget reached; returning %d partial "
                    "jobs from %d/%d completed tasks (remaining cancelled).",
                    self._time_budget_s, len(jobs), done, len(tasks),
                )
        finally:
            # Cancel queued-but-unstarted tasks (stop spending calls); don't block on the
            # <=max_workers already in flight — they finish harmlessly in the background.
            executor.shutdown(wait=False, cancel_futures=True)

        s.log_result(jobs)
        return jobs

    @classmethod
    def make(cls, adzuna_config, titles: list[str], max_workers: int = _DEFAULT_WORKERS,
             relevant_keywords: list[str] | None = None,
             excluded_keywords: list[str] | None = None,
             what_exclude: list[str] | None = None,
             max_calls_per_minute: int = _DEFAULT_MAX_CALLS_PER_MINUTE,
             max_calls_per_run: int = _DEFAULT_MAX_CALLS_PER_RUN):
        """Instantiate the v1 AdzunaScraper and wrap it. Returns None on failure.

        ADR-064: relevant_keywords/excluded_keywords flow through to the v1
        scraper's title-relevance gate so a per-run search can override the
        senior defaults (e.g. role-derived tokens for an entry-level profile).
        ADR-065: what_exclude is passed to Adzuna's query so senior terms are
        dropped at the source.
        ADR-107/108: max_calls_per_minute paces calls under the per-minute cap;
        max_calls_per_run bounds total calls (one per title x location + remote).
        """
        try:
            from scrapers.adzuna import AdzunaScraper
            v1 = AdzunaScraper(adzuna_config, titles,
                               relevant_keywords=relevant_keywords,
                               excluded_keywords=excluded_keywords,
                               what_exclude=what_exclude)
            return cls(v1, max_workers=max_workers,
                       max_calls_per_minute=max_calls_per_minute,
                       max_calls_per_run=max_calls_per_run)
        except Exception as exc:
            logger.warning("ConcurrentAdzunaScraper.make failed: %s", exc)
            return None
