"""Best-effort dead-link filter (ADR-095).

Opt-in discovery step that drops postings whose apply link is *verifiably* dead, so
a scored match (and especially a "where to focus" pick) never points at a 404. It is
**conservative by design**: it drops ONLY on hard signals and keeps the job on any
ambiguity, because full link verification was previously shown unreliable (Adzuna
429s, JS-gated apply pages, soft-expiry - the ADR-080/081 dead-link arc).

Verdict per URL (`check_link`):
  - "dead"    -> final HTTP status 404/410, or a 200 page whose body contains a
                 known closed-job marker ("no longer available", ...). DROP.
  - "alive"   -> 200 with no closed marker. KEEP.
  - "unknown" -> timeout / connection error / DNS failure / 429 / 5xx / non-http
                 URL. KEEP (never lose a job to a transient or ambiguous signal).

Network I/O, so it only runs when the profile opts in (search.drop_dead_links) and is
bounded by a short per-URL timeout + capped concurrency.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_S = 4.0
_MAX_CONCURRENCY = 10
_UA = "Mozilla/5.0 (compatible; JobSearchAgent/2.0; link-health-check)"
_BODY_SCAN_CHARS = 20_000

_DEAD_STATUS = {404, 410}
# Closed-job phrases ATS/boards show on a 200 page when the req is pulled/filled.
_DEAD_MARKERS = (
    "no longer available",
    "no longer accepting applications",
    "this job is closed",
    "this position is no longer",
    "this job is no longer",
    "position has been filled",
    "posting is no longer active",
    "job you are looking for is no longer",
    "this requisition is closed",
    "job posting not found",
)


def check_link(url: str | None, *, timeout: float = _TIMEOUT_S,
               client: httpx.Client | None = None) -> str:
    """Return "dead" | "alive" | "unknown" for a single apply URL. Follows
    redirects (aggregator links point at the real posting). Never raises."""
    if not url or not str(url).lower().startswith(("http://", "https://")):
        return "unknown"
    own = client is None
    c = client or httpx.Client(follow_redirects=True, timeout=timeout,
                               headers={"User-Agent": _UA})
    try:
        resp = c.get(url)
    except httpx.HTTPError:
        return "unknown"  # timeout / connection / DNS / transient -> keep
    finally:
        if own:
            c.close()
    code = resp.status_code
    if code in _DEAD_STATUS:
        return "dead"
    if code == 200:
        body = (resp.text or "")[:_BODY_SCAN_CHARS].lower()
        return "dead" if any(m in body for m in _DEAD_MARKERS) else "alive"
    return "unknown"  # 429 / 5xx / unexpected -> keep


def filter_dead_links(postings, *, timeout: float = _TIMEOUT_S,
                      max_workers: int = _MAX_CONCURRENCY,
                      client: httpx.Client | None = None):
    """Concurrently check each posting's URL and drop the verifiably-dead ones.

    Returns (kept, dropped_count, dropped_sample). Only "dead" verdicts are dropped;
    "alive" and "unknown" are kept (never-lose-the-run). `client` is injectable for
    tests; otherwise a short-timeout, redirect-following client is created.
    """
    postings = list(postings or [])
    if not postings:
        return postings, 0, []
    own = client is None
    c = client or httpx.Client(follow_redirects=True, timeout=timeout,
                               headers={"User-Agent": _UA})
    try:
        def _verdict(item):
            i, posting = item
            return i, check_link(getattr(posting, "url", None),
                                 timeout=timeout, client=c)
        verdicts: dict[int, str] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for i, v in ex.map(_verdict, list(enumerate(postings))):
                verdicts[i] = v
    finally:
        if own:
            c.close()

    kept, dropped = [], []
    for i, p in enumerate(postings):
        (dropped if verdicts.get(i) == "dead" else kept).append(p)
    sample = [{"title": getattr(p, "title", ""), "url": getattr(p, "url", "")}
              for p in dropped[:10]]
    if dropped:
        logger.info("dead_link_filter: dropped %d of %d postings (verifiably dead)",
                    len(dropped), len(postings))
    return kept, len(dropped), sample
