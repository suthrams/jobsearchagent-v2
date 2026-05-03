"""Tests for CustomUrlScraper — fetch + heuristic + LLM fallback flow."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.providers.llm_client import LLMProviderError
from app.services.custom_url_scraper import CustomUrlScraper


# ── URL list normalisation ────────────────────────────────────────────────────

def test_init_dedupes_strips_and_caps_urls():
    raw = [
        "  https://example.com/a  ",
        "https://example.com/a",      # duplicate after strip
        "https://example.com/b",
        "not-a-url",                  # rejected (no scheme)
        "ftp://example.com/c",        # rejected (wrong scheme)
        "",                           # empty
    ]
    scraper = CustomUrlScraper(raw)
    assert scraper._urls == ["https://example.com/a", "https://example.com/b"]


def test_init_caps_urls_at_max():
    urls = [f"https://example.com/{i}" for i in range(50)]
    scraper = CustomUrlScraper(urls)
    assert len(scraper._urls) == 25


# ── Heuristic JSON-LD extraction ──────────────────────────────────────────────

_JSON_LD_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@type": "JobPosting",
  "title": "Staff Software Engineer",
  "hiringOrganization": {"name": "Acme Corp"},
  "jobLocation": {"address": {"addressLocality": "Austin", "addressRegion": "TX"}},
  "jobLocationType": "TELECOMMUTE",
  "description": "<p>Build distributed systems in Python. Lead architecture across teams. Mentor senior engineers. Drive technical strategy across multiple products and ensure operational excellence at scale and across all environments. We expect significant ownership of design decisions, on-call rotations, and partnership with product, security, and infra leadership to deliver durable platforms.</p>"
}
</script>
<title>Job - Acme</title>
</head><body><article>Filler body content.</article></body></html>
"""


def test_heuristic_extracts_from_json_ld(monkeypatch):
    scraper = CustomUrlScraper(["https://example.com/job/1"])
    monkeypatch.setattr(scraper, "_fetch", lambda url: _JSON_LD_HTML)
    jobs = scraper.scrape()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Staff Software Engineer"
    assert job.company == "Acme Corp"
    assert "Austin" in (job.location or "")
    assert job.work_mode == "remote"
    assert "distributed systems" in (job.description or "")


# ── LLM fallback when heuristics insufficient ─────────────────────────────────

_THIN_HTML = "<html><head><title>Hello</title></head><body><p>Apply now</p></body></html>"


def test_llm_fallback_invoked_when_heuristics_thin():
    llm = MagicMock()
    llm.complete.return_value = {
        "title": "Principal Engineer",
        "company": "WidgetCo",
        "location": "Remote",
        "work_mode": "remote",
        "description": "x" * 500,  # comfortably above min_description_chars
        "salary_min": 200000,
        "salary_max": 300000,
        "salary_currency": "USD",
    }
    scraper = CustomUrlScraper(["https://example.com/job/2"], llm_client=llm)
    with patch.object(scraper, "_fetch", return_value=_THIN_HTML):
        jobs = scraper.scrape()
    assert len(jobs) == 1
    assert jobs[0].title == "Principal Engineer"
    assert jobs[0].salary.min == 200000
    llm.complete.assert_called_once()


def test_llm_fallback_unavailable_records_error():
    scraper = CustomUrlScraper(["https://example.com/job/3"])  # no llm_client
    with patch.object(scraper, "_fetch", return_value=_THIN_HTML):
        jobs = scraper.scrape()
    assert jobs == []
    errs = scraper.errors()
    assert len(errs) == 1
    assert "no LLM client" in errs[0]["reason"]


def test_llm_failure_recorded_as_error():
    llm = MagicMock()
    llm.complete.side_effect = LLMProviderError("rate limited and exhausted")
    scraper = CustomUrlScraper(["https://example.com/job/4"], llm_client=llm)
    with patch.object(scraper, "_fetch", return_value=_THIN_HTML):
        jobs = scraper.scrape()
    assert jobs == []
    errs = scraper.errors()
    assert len(errs) == 1
    assert "llm extraction failed" in errs[0]["reason"]


def test_fetch_failure_recorded_as_error():
    scraper = CustomUrlScraper(["https://example.com/dead"])
    err = httpx.ConnectError("DNS fail")
    with patch.object(scraper, "_fetch", side_effect=err):
        jobs = scraper.scrape()
    assert jobs == []
    errs = scraper.errors()
    assert len(errs) == 1
    assert "fetch failed" in errs[0]["reason"]
