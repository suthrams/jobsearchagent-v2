"""Workday ATS-direct scraper (ADR-101).

The source-of-truth feed for the cleared-government employers BUG-010 cares about
(Booz Allen, Leidos, CACI, GDIT, ...) and most of the F500 - they post on Workday,
not on the Greenhouse/Lever boards ADR-081 supports. Workday exposes an undocumented
but consistent JSON "CXS" API (de-risked in `docs/architecture/spike_workday_ats.md`)
that returns FULL job descriptions (5-8k chars) - so the deterministic clearance /
experience filters finally see the real requirement text instead of Adzuna's truncated
~500-char snippet.

Why its own module (not folded into `ats_scrapers.py`): unlike Greenhouse/Lever (one
slug, one GET), Workday carries genuinely new, self-contained complexity -

  * a 3-part board id (`tenant` + datacenter + `site`) pasted as a career URL, which
    needs PARSING and an SSRF host guard (`parse_workday_url`);
  * a TWO-PHASE fetch: list (paginated, bounded) -> title-filter the listing BEFORE
    any detail fetch -> capped per-board detail fetch for the full JD. The title gate
    is the load-bearing volume control: never fetch JDs for the 1-2k jobs a board
    lists, only for the handful matching the run's roles;
  * a relative `postedOn` string ("Posted 5 Days Ago") that needs a best-effort parser.

`ats_scrapers.py` keeps only the shared seam (`build_ats_scrapers`, `verify_ats_board`)
which lazy-imports this module; the shared `_strip_html` / `_title_ok` helpers are
reused from there (no import cycle: `ats_scrapers` only imports this module from inside
its functions, long after both modules have finished loading).

The contract (undocumented -> best-effort, never-lose-the-run: any board/detail failure
is logged + skipped, never raised):
  List:   POST https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
          {"appliedFacets": {}, "limit": 20, "offset": N, "searchText": <role|"">}
          -> {"total", "jobPostings": [{title, externalPath, locationsText, postedOn}]}
  Detail: GET  https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}
          -> {"jobPostingInfo": {jobDescription (HTML), location, startDate, ...}}
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

from app.services.ats_scrapers import _strip_html, _title_ok
from models.job import Job, JobSource
from scrapers.base import BaseScraper

# SSRF guard: only `{tenant}.{dc}.myworkdayjobs.com` hosts are ever fetched. Both the
# scraper and the verify endpoint parse through `parse_workday_url`, so an arbitrary
# user-supplied host can never become a request target.
_HOST_RE = re.compile(r"^([a-z0-9][a-z0-9-]*)\.([a-z0-9][a-z0-9-]*)\.myworkdayjobs\.com$", re.I)
# A leading career-path segment like `en-US` / `fr` is a locale, not the Workday `site`.
_LOCALE_RE = re.compile(r"^[a-z]{2}(-[a-z]{2})?$", re.I)
_NUM_DAYS_RE = re.compile(r"(\d+)\+?\s*day", re.I)
_NUM_MONTHS_RE = re.compile(r"(\d+)\+?\s*month", re.I)

_USER_AGENT = "Mozilla/5.0 (compatible; jobsearchagent/2.0; +ats-direct)"
_HEADERS = {"User-Agent": _USER_AGENT, "Accept": "application/json"}

_TIMEOUT_S = 15.0
_LIST_PAGE_SIZE = 20
# Volume control (the spike's open risk). Per role-query we page at most this many
# times (5 * 20 = 100 listed), then title-filter, then fetch at most N full JDs. A
# board that lists 2,000 jobs costs <= queries*5 list calls + 25 detail calls, not 2k.
_MAX_LIST_PAGES = 5
_MAX_DETAILS_PER_BOARD = 25
_MAX_QUERIES = 5            # cap how many roles become server-side searchText queries
_DEFAULT_WORKERS = 8       # boards fetched concurrently, like Greenhouse (ADR-097)

# A board id resolved from a career URL: (tenant, dc, site). Tuple so it is hashable
# (used as a future-map key) and order-stable.
Board = tuple


def parse_workday_url(url: str | None) -> tuple[str, str, str] | None:
    """Parse a Workday career URL into `(tenant, dc, site)`, or `None` if it is not a
    valid `*.myworkdayjobs.com` URL.

    This is the ONLY place a URL becomes a board id and the ONLY place the host guard
    runs (ADR-101): the scraper and the verify endpoint both route through it, so an
    arbitrary user URL is never fetched. Examples:
      https://leidos.wd5.myworkdayjobs.com/en-US/External -> ("leidos", "wd5", "External")
      https://bah.wd1.myworkdayjobs.com/BAH_Jobs          -> ("bah", "wd1", "BAH_Jobs")
    """
    if not url or not url.strip():
        return None
    raw = url.strip()
    if "://" not in raw:
        raw = "https://" + raw  # tolerate a bare host paste; the host guard still applies
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    m = _HOST_RE.match(host)
    if not m:
        return None  # not a tenant.dc.myworkdayjobs.com host -> reject (SSRF guard)
    tenant, dc = m.group(1), m.group(2)
    segments = [s for s in parsed.path.split("/") if s]
    if segments and _LOCALE_RE.match(segments[0]):
        segments = segments[1:]  # drop a leading locale; the site is the next segment
    if not segments:
        return None
    return (tenant, dc, segments[0])


def _normalize_boards(boards) -> list[tuple[str, str, str]]:
    """Accept the stored `{tenant, dc, site}` dicts (or 3-tuples) and return clean,
    complete triples. Malformed/partial entries are dropped, not raised."""
    out: list[tuple[str, str, str]] = []
    for b in boards or []:
        if isinstance(b, dict):
            t, d, s = b.get("tenant"), b.get("dc"), b.get("site")
        elif isinstance(b, (list, tuple)) and len(b) == 3:
            t, d, s = b
        else:
            continue
        t, d, s = (t or "").strip(), (d or "").strip(), (s or "").strip()
        if t and d and s:
            out.append((t, d, s))
    return out


def _parse_relative_posted(raw) -> datetime | None:
    """Best-effort parse of Workday's relative `postedOn` ("Posted 5 Days Ago",
    "Posted Today", "Posted 30+ Days Ago"). Unknown shapes -> `None` (ADR-080 keeps
    unknown-age postings, so a miss never silently drops a job)."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip().lower()
    now = datetime.now(timezone.utc)
    if "today" in s:
        return now
    if "yesterday" in s:
        return now - timedelta(days=1)
    m = _NUM_DAYS_RE.search(s)
    if m:
        return now - timedelta(days=int(m.group(1)))
    m = _NUM_MONTHS_RE.search(s)
    if m:
        return now - timedelta(days=30 * int(m.group(1)))
    return None


class WorkdayScraper(BaseScraper):
    """Pulls public Workday CXS boards for a list of `(tenant, dc, site)` triples."""

    _BASE = "https://{tenant}.{dc}.myworkdayjobs.com"
    _LIST_PATH = "/wday/cxs/{tenant}/{site}/jobs"
    _DETAIL_PATH = "/wday/cxs/{tenant}/{site}{external_path}"

    def __init__(self, boards, roles: list[str] | None = None,
                 relevant_tokens: list[str] | None = None,
                 timeout_s: float = _TIMEOUT_S, max_workers: int = _DEFAULT_WORKERS) -> None:
        super().__init__("workday")
        self._boards = _normalize_boards(boards)
        # The run's roles become server-side `searchText` queries so the bounded
        # listing is relevant jobs, not the first 100 of everything (ADR-101).
        self._roles = [r.strip() for r in (roles or []) if r and r.strip()]
        self._relevant = [t.lower() for t in (relevant_tokens or [])]
        self._timeout = timeout_s
        self._max_workers = max_workers

    def scrape(self) -> list[Job]:
        jobs: list[Job] = []
        if not self._boards:
            self.log_result(jobs)
            return jobs
        # Boards run concurrently (one worker per board); a per-board failure is logged
        # and skipped. Results are collected in board order for determinism.
        with httpx.Client(timeout=self._timeout, follow_redirects=True, headers=_HEADERS) as client:
            with ThreadPoolExecutor(max_workers=min(self._max_workers, len(self._boards))) as ex:
                futures = {board: ex.submit(self._fetch_board, client, *board)
                           for board in self._boards}
            for board in self._boards:
                try:
                    jobs.extend(futures[board].result())
                except Exception as exc:
                    self.logger.warning("workday board %s failed: %s", "/".join(board), exc)
        self.log_result(jobs)
        return jobs

    def _fetch_board(self, client: httpx.Client, tenant: str, dc: str, site: str) -> list[Job]:
        base = self._BASE.format(tenant=tenant, dc=dc)
        list_url = base + self._LIST_PATH.format(tenant=tenant, site=site)

        # Phase 1+2: list (bounded) and title-filter BEFORE any detail fetch, deduped
        # across role queries by externalPath. This is the load-bearing volume control.
        listings: dict[str, dict] = {}
        for query in (self._roles[:_MAX_QUERIES] or [""]):
            for page in range(_MAX_LIST_PAGES):
                body = {"appliedFacets": {}, "limit": _LIST_PAGE_SIZE,
                        "offset": page * _LIST_PAGE_SIZE, "searchText": query}
                resp = client.post(list_url, json=body)
                resp.raise_for_status()
                data = resp.json() or {}
                postings = data.get("jobPostings") or []
                if not postings:
                    break
                for p in postings:
                    ep = p.get("externalPath")
                    if (ep and ep not in listings
                            and _title_ok(p.get("title") or "", self._relevant)):
                        listings[ep] = p
                total = data.get("total")
                if isinstance(total, int) and (page + 1) * _LIST_PAGE_SIZE >= total:
                    break

        # Phase 3: capped detail fetch for the full JD. Sequential within a board so the
        # board-level pool stays the only concurrency dimension (no nested fan-out); a
        # failed/slow detail skips that one job, never the board (never-lose-the-run).
        out: list[Job] = []
        for ep, listing in list(listings.items())[:_MAX_DETAILS_PER_BOARD]:
            try:
                job = self._fetch_detail(client, base, tenant, site, ep, listing)
            except Exception as exc:
                self.logger.debug("workday detail %s%s failed: %s", site, ep, exc)
                continue
            if job is not None:
                out.append(job)
        return out

    def _fetch_detail(self, client: httpx.Client, base: str, tenant: str, site: str,
                      external_path: str, listing: dict) -> Job | None:
        detail_url = base + self._DETAIL_PATH.format(
            tenant=tenant, site=site, external_path=external_path)
        resp = client.get(detail_url)
        resp.raise_for_status()
        info = ((resp.json() or {}).get("jobPostingInfo")) or {}
        title = listing.get("title") or info.get("title")
        if not title:
            return None
        # Apply URL is the employer's own Workday career page (employer-direct, no
        # redirect / dead-link issue, ADR-093).
        apply_url = info.get("externalUrl") or f"{base}/{site}{external_path}"
        return Job(
            url=apply_url,
            source=JobSource.WORKDAY,
            title=title,
            company=tenant,
            location=listing.get("locationsText") or info.get("location"),
            description=_strip_html(info.get("jobDescription")),
            posted_at=_parse_relative_posted(listing.get("postedOn") or info.get("startDate")),
        )


def verify_workday_board(url: str | None, timeout_s: float = 8.0) -> int | None:
    """Live-check one Workday career URL (ADR-101 verify-on-add). Parses + host-guards
    the URL, probes the list endpoint, and returns the board's open-job `total` for a
    healthy board, else `None` (bad URL, non-200, or unreachable). One bounded POST, no
    retries - the same single source of truth the Settings verify-on-add and
    `tools/verify_ats_boards.py` share for Greenhouse/Lever."""
    parsed = parse_workday_url(url)
    if not parsed:
        return None
    tenant, dc, site = parsed
    list_url = (WorkdayScraper._BASE.format(tenant=tenant, dc=dc)
                + WorkdayScraper._LIST_PATH.format(tenant=tenant, site=site))
    body = {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}
    try:
        resp = httpx.post(list_url, json=body, timeout=timeout_s,
                          headers=_HEADERS, follow_redirects=True)
        if resp.status_code != 200:
            return None
        data = resp.json() or {}
        total = data.get("total")
        if isinstance(total, int):
            return total
        postings = data.get("jobPostings")
        return len(postings) if isinstance(postings, list) else None
    except Exception:
        return None
