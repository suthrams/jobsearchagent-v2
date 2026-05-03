"""CustomUrlScraper — fetches arbitrary job-posting URLs and produces JobDiscoveryService-compatible objects.

Per URL, in order:
  1. httpx GET with desktop UA, 30s timeout. HTTP errors → skip + record fetch_error.
  2. Heuristic parse: JSON-LD JobPosting schema → OpenGraph tags → common meta selectors.
     If we get title + a non-trivial description, we're done.
  3. LLM fallback: clean HTML to text and ask Claude to extract structured fields.
  4. If both fail to produce a title or description, skip + record extraction_error.

The scraper is constructed per workflow run with the user-supplied URL list and
appended to JobDiscoveryService scrapers for that run only.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel

from app.providers.llm_client import LLMClient, LLMProviderError

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_DEFAULT_TIMEOUT_S = 30.0
_MAX_URLS = 25
_MAX_HTML_CHARS = 80_000  # cap LLM input at ~20k tokens worst case
_MIN_DESCRIPTION_CHARS = 200  # below this, treat heuristics as insufficient


# ── LLM extraction schema ──────────────────────────────────────────────────────

class _CustomJobExtraction(BaseModel):
    """Schema Claude returns when extracting a job posting from page text."""
    title: str | None = None
    company: str | None = None
    location: str | None = None
    work_mode: str | None = None  # "remote" | "hybrid" | "onsite" | None
    description: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None


# ── Result containers ──────────────────────────────────────────────────────────

@dataclass
class CustomUrlResult:
    url: str
    job: SimpleNamespace | None = None
    error: str | None = None
    extraction_method: str | None = None  # "heuristic" | "llm" | None


@dataclass
class CustomUrlScraperOutput:
    """Holds both the produced jobs and per-URL fetch_errors for the workflow state."""
    jobs: list[SimpleNamespace] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)


# ── Scraper ────────────────────────────────────────────────────────────────────

class CustomUrlScraper:
    """Bounded scraper that fetches a user-supplied URL list and yields v1-shaped jobs.

    The orchestrator instantiates this per workflow run with the URLs from the
    StartWorkflowRequest. JobDiscoveryService calls scrape() like any other scraper.
    """

    name = "custom_urls"

    def __init__(
        self,
        urls: list[str],
        llm_client: LLMClient | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        # Dedup, strip, drop empties, cap.
        seen: set[str] = set()
        cleaned: list[str] = []
        for raw in urls:
            u = (raw or "").strip()
            if not u or u in seen:
                continue
            if not u.lower().startswith(("http://", "https://")):
                continue
            seen.add(u)
            cleaned.append(u)
            if len(cleaned) >= _MAX_URLS:
                break
        self._urls = cleaned
        self._llm = llm_client
        self._timeout = timeout_s
        self._errors: list[dict] = []

    # ── BaseScraper-compatible entry point ────────────────────────────────────

    def scrape(self) -> list[SimpleNamespace]:
        """Run all URLs, return jobs. Per-URL errors stored on self.errors()."""
        jobs: list[SimpleNamespace] = []
        self._errors = []
        for url in self._urls:
            res = self._scrape_one(url)
            if res.job is not None:
                jobs.append(res.job)
                logger.info("custom_urls: %s extracted via %s", url, res.extraction_method)
            else:
                self._errors.append({"url": url, "reason": res.error or "unknown"})
                logger.warning("custom_urls: %s skipped — %s", url, res.error)
        return jobs

    def errors(self) -> list[dict]:
        """Return per-URL errors collected during the last scrape() invocation."""
        return list(self._errors)

    # ── One URL ───────────────────────────────────────────────────────────────

    def _scrape_one(self, url: str) -> CustomUrlResult:
        # 1. fetch
        try:
            html = self._fetch(url)
        except httpx.HTTPError as exc:
            return CustomUrlResult(url=url, error=f"fetch failed: {exc}")
        if not html:
            return CustomUrlResult(url=url, error="empty response body")

        # 2. heuristic
        fields = self._extract_heuristic(html, url)
        method: str | None = "heuristic"
        if not self._is_sufficient(fields):
            # 3. LLM fallback
            if self._llm is None:
                return CustomUrlResult(
                    url=url,
                    error="heuristics insufficient and no LLM client configured",
                )
            try:
                fields = self._extract_via_llm(html, url, prior=fields)
                method = "llm"
            except LLMProviderError as exc:
                return CustomUrlResult(url=url, error=f"llm extraction failed: {exc}")
            except Exception as exc:
                return CustomUrlResult(url=url, error=f"llm extraction error: {exc}")

        if not self._is_sufficient(fields):
            return CustomUrlResult(url=url, error="extraction yielded no usable title/description")

        return CustomUrlResult(
            url=url,
            job=self._build_job(url, fields),
            extraction_method=method,
        )

    # ── Fetch ─────────────────────────────────────────────────────────────────

    def _fetch(self, url: str) -> str:
        with httpx.Client(
            follow_redirects=True,
            timeout=self._timeout,
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.text or ""

    # ── Heuristic extraction ──────────────────────────────────────────────────

    @staticmethod
    def _extract_heuristic(html: str, url: str) -> dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        out: dict[str, Any] = {}

        # JSON-LD JobPosting
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            for entry in (data if isinstance(data, list) else [data]):
                if not isinstance(entry, dict):
                    continue
                if entry.get("@type") in ("JobPosting", ["JobPosting"]):
                    out.setdefault("title", entry.get("title"))
                    org = entry.get("hiringOrganization") or {}
                    if isinstance(org, dict):
                        out.setdefault("company", org.get("name"))
                    loc = entry.get("jobLocation") or {}
                    if isinstance(loc, list) and loc:
                        loc = loc[0]
                    if isinstance(loc, dict):
                        addr = loc.get("address") or {}
                        if isinstance(addr, dict):
                            out.setdefault("location",
                                           ", ".join(filter(None, [addr.get("addressLocality"),
                                                                    addr.get("addressRegion")])))
                    desc = entry.get("description")
                    if desc and "description" not in out:
                        out["description"] = BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)
                    if entry.get("jobLocationType") == "TELECOMMUTE":
                        out.setdefault("work_mode", "remote")

        # OpenGraph fallbacks
        def _og(prop: str) -> str | None:
            tag = soup.find("meta", attrs={"property": prop})
            return tag.get("content") if tag and tag.get("content") else None

        out.setdefault("title", _og("og:title") or (soup.title.string.strip() if soup.title and soup.title.string else None))
        out.setdefault("description", _og("og:description"))
        out.setdefault("company", _og("og:site_name"))

        # Best-effort full-text description if still empty
        if not out.get("description"):
            article = soup.find("article") or soup.find("main")
            if article:
                text = article.get_text(" ", strip=True)
                if len(text) >= _MIN_DESCRIPTION_CHARS:
                    out["description"] = text[:8000]

        # Normalise whitespace
        for k, v in list(out.items()):
            if isinstance(v, str):
                out[k] = re.sub(r"\s+", " ", v).strip() or None
        return out

    @staticmethod
    def _is_sufficient(fields: dict) -> bool:
        title = (fields.get("title") or "").strip()
        desc = (fields.get("description") or "").strip()
        return bool(title) and len(desc) >= _MIN_DESCRIPTION_CHARS

    # ── LLM fallback ──────────────────────────────────────────────────────────

    def _extract_via_llm(self, html: str, url: str, prior: dict) -> dict:
        text = self._html_to_text(html)
        if not text:
            return prior
        # Truncate to keep token cost bounded
        text = text[:_MAX_HTML_CHARS]
        result = self._llm.complete(
            agent_name="custom_url_extractor",
            context={"url": url, "page_text": text, "prior_heuristics": prior},
            schema=_CustomJobExtraction,
        )
        # Merge: LLM wins where it produced a value, prior fills gaps
        merged = dict(prior)
        for key, value in result.items():
            if value not in (None, "", []):
                merged[key] = value
        return merged

    @staticmethod
    def _html_to_text(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        return re.sub(r"\s+", " ", text).strip()

    # ── Build v1-Job-shaped object ────────────────────────────────────────────

    @staticmethod
    def _build_job(url: str, fields: dict) -> SimpleNamespace:
        """Return an object with the same attributes JobDiscoveryService.normalize() reads.

        A SimpleNamespace is intentional: the v1 Job pydantic model would reject
        unknown source values and force us to extend the v1 enum. JobDiscoveryService
        uses getattr() throughout, so a duck-typed object works cleanly.
        """
        salary = None
        if fields.get("salary_min") or fields.get("salary_max"):
            salary = SimpleNamespace(
                min=fields.get("salary_min"),
                max=fields.get("salary_max"),
                currency=fields.get("salary_currency") or "USD",
            )
        return SimpleNamespace(
            url=url,
            source="manual",  # → JobSource.MANUAL via _SOURCE_MAP fallback
            title=(fields.get("title") or "").strip() or "Untitled role",
            company=(fields.get("company") or "Unknown").strip(),
            location=(fields.get("location") or None),
            work_mode=fields.get("work_mode"),
            description=fields.get("description"),
            salary=salary,
            found_at=datetime.now(tz=timezone.utc),
            posted_at=None,
        )
