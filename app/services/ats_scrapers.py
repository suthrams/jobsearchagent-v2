"""ATS-direct scrapers: Greenhouse + Lever (ADR-081, spike/prototype).

Source-of-truth job feeds. Unlike aggregators (Adzuna), an ATS board only returns
CURRENTLY PUBLISHED postings and the apply URL is the employer's own ATS-hosted
page - so the dead-apply-link problem (ADR-080) does not arise, and these endpoints
are not bot-blocked. The tradeoff is they are queried PER COMPANY (by board token /
slug), so they are driven by a curated company list in config.

Both implement the v1 `BaseScraper` interface (return `list[Job]`), so
`JobDiscoveryService` normalizes + dedups them exactly like Adzuna. Title relevance
reuses the role-derived tokens (ADR-064) so only postings matching the run's roles
survive a full-board dump.

Field mappings verified against the live APIs 2026-06-04:
  Greenhouse  boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
    absolute_url -> url, title, company_name -> company, location.name -> location,
    first_published|updated_at -> posted_at, content (HTML) -> description.
  Lever       api.lever.co/v0/postings/{slug}?mode=json
    text -> title, hostedUrl -> url, categories.location -> location,
    createdAt (epoch ms) -> posted_at, descriptionPlain -> description; company = slug.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from models.filters import EXCLUDED_TITLE_KEYWORDS
from models.job import Job, JobSource
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_TIMEOUT_S = 15.0
# A single board can list hundreds of roles; bound what we pull per board before
# the title-relevance gate so one big company can't dominate a run.
_MAX_JOBS_PER_BOARD = 100
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(raw: str | None) -> str | None:
    """HTML -> plain text: unescape entities, drop tags, collapse whitespace.

    Greenhouse `content` is HTML-entity-escaped HTML; the scoring/research agents
    want plain text, not markup.
    """
    if not raw:
        return None
    text = html.unescape(raw)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def _title_ok(title: str, relevant: list[str]) -> bool:
    """Keep a title only if it matches a relevant token (when given) and no
    excluded keyword. Mirrors the Adzuna per-run title gate (ADR-064)."""
    t = (title or "").lower()
    if relevant and not any(tok in t for tok in relevant):
        return False
    if any(kw in t for kw in EXCLUDED_TITLE_KEYWORDS):
        return False
    return True


class GreenhouseScraper(BaseScraper):
    """Pulls public Greenhouse job boards for a list of company board tokens."""

    _URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"

    def __init__(self, tokens: list[str], relevant_tokens: list[str] | None = None,
                 timeout_s: float = _TIMEOUT_S) -> None:
        super().__init__("greenhouse")
        self._tokens = [t.strip() for t in tokens if t and t.strip()]
        self._relevant = [t.lower() for t in (relevant_tokens or [])]
        self._timeout = timeout_s

    def scrape(self) -> list[Job]:
        jobs: list[Job] = []
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
            for token in self._tokens:
                try:
                    resp = client.get(self._URL.format(token=token))
                    resp.raise_for_status()
                    items = (resp.json() or {}).get("jobs") or []
                except Exception as exc:
                    self.logger.warning("greenhouse board %s failed: %s", token, exc)
                    continue
                for item in items[:_MAX_JOBS_PER_BOARD]:
                    if not _title_ok(item.get("title") or "", self._relevant):
                        continue
                    job = self._to_job(item, token)
                    if job is not None:
                        jobs.append(job)
        self.log_result(jobs)
        return jobs

    @staticmethod
    def _to_job(item: dict, token: str) -> Job | None:
        url = item.get("absolute_url")
        title = item.get("title")
        if not url or not title:
            return None
        loc = item.get("location") or {}
        posted = _parse_iso(item.get("first_published") or item.get("updated_at"))
        return Job(
            url=url,
            source=JobSource.GREENHOUSE,
            title=title,
            company=item.get("company_name") or token,
            location=(loc.get("name") if isinstance(loc, dict) else None),
            description=_strip_html(item.get("content")),
            posted_at=posted,
        )


class LeverScraper(BaseScraper):
    """Pulls public Lever postings for a list of company slugs."""

    _URL = "https://api.lever.co/v0/postings/{slug}?mode=json"

    def __init__(self, slugs: list[str], relevant_tokens: list[str] | None = None,
                 timeout_s: float = _TIMEOUT_S) -> None:
        super().__init__("lever")
        self._slugs = [s.strip() for s in slugs if s and s.strip()]
        self._relevant = [t.lower() for t in (relevant_tokens or [])]
        self._timeout = timeout_s

    def scrape(self) -> list[Job]:
        jobs: list[Job] = []
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
            for slug in self._slugs:
                try:
                    resp = client.get(self._URL.format(slug=slug))
                    resp.raise_for_status()
                    items = resp.json() or []
                except Exception as exc:
                    self.logger.warning("lever board %s failed: %s", slug, exc)
                    continue
                for item in items[:_MAX_JOBS_PER_BOARD]:
                    if not _title_ok(item.get("text") or "", self._relevant):
                        continue
                    job = self._to_job(item, slug)
                    if job is not None:
                        jobs.append(job)
        self.log_result(jobs)
        return jobs

    @staticmethod
    def _to_job(item: dict, slug: str) -> Job | None:
        url = item.get("hostedUrl")
        title = item.get("text")
        if not url or not title:
            return None
        cats = item.get("categories") or {}
        return Job(
            url=url,
            source=JobSource.LEVER,
            title=title,
            company=slug,
            location=(cats.get("location") if isinstance(cats, dict) else None),
            description=item.get("descriptionPlain") or _strip_html(item.get("description")),
            posted_at=_parse_epoch_ms(item.get("createdAt")),
        )


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _parse_epoch_ms(raw: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def build_ats_scrapers(roles: list[str], scrapers_cfg: dict) -> list[BaseScraper]:
    """Build the configured ATS scrapers for a run (ADR-081).

    Reads `scrapers.greenhouse.companies` and `scrapers.lever.companies` from the
    config; title relevance derives from the run's `roles` (falls back to no gate
    when roles are absent). Returns [] when nothing is configured, so ATS discovery
    is purely additive and off until a profile lists target companies.
    """
    from app.services.concurrent_adzuna_scraper import relevance_tokens

    cfg = scrapers_cfg or {}
    relevant = relevance_tokens(roles) if roles else []
    out: list[BaseScraper] = []

    gh = cfg.get("greenhouse") or {}
    gh_companies = list(gh.get("companies") or [])
    if gh.get("enabled", True) and gh_companies:
        out.append(GreenhouseScraper(gh_companies, relevant_tokens=relevant))

    lv = cfg.get("lever") or {}
    lv_companies = list(lv.get("companies") or [])
    if lv.get("enabled", True) and lv_companies:
        out.append(LeverScraper(lv_companies, relevant_tokens=relevant))

    return out
