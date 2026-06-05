"""ADR-081: ATS-direct scrapers (Greenhouse + Lever).

Mapping fixtures mirror the live API shapes verified 2026-06-04. httpx is patched
so the tests make no network calls.
"""
from __future__ import annotations

from datetime import timezone

import httpx
import pytest

from app.services.ats_scrapers import (
    GreenhouseScraper,
    LeverScraper,
    _strip_html,
    build_ats_scrapers,
)
from models.job import JobSource

# ── fixtures shaped like the real responses ──────────────────────────────────

_GH = {"jobs": [
    {"absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
     "id": 1, "title": "Software Engineer", "company_name": "Acme",
     "location": {"name": "Remote"}, "first_published": "2026-05-08T10:53:12-04:00",
     "content": "&lt;p&gt;Build &amp; ship &lt;b&gt;things&lt;/b&gt;.&lt;/p&gt;"},
    {"absolute_url": "https://job-boards.greenhouse.io/acme/jobs/2",
     "id": 2, "title": "Warehouse Forklift Operator", "company_name": "Acme",
     "location": {"name": "Ohio"}, "first_published": "2026-05-01T00:00:00Z",
     "content": "&lt;p&gt;Lift.&lt;/p&gt;"},
]}

_LEVER = [
    {"id": "abc", "text": "Software Engineer",
     "hostedUrl": "https://jobs.lever.co/acme/abc",
     "categories": {"location": "Austin, TX"}, "createdAt": 1700000000000,
     "descriptionPlain": "Write code."},
    {"id": "def", "text": "Account Executive",
     "hostedUrl": "https://jobs.lever.co/acme/def",
     "categories": {"location": "NYC"}, "createdAt": 1700000000000,
     "descriptionPlain": "Sell things."},
]


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def get(self, url):
        return httpx.Response(200, json=self._payload, request=httpx.Request("GET", url))


@pytest.fixture
def _patch_httpx(monkeypatch):
    def _factory(payload):
        monkeypatch.setattr(httpx, "Client", lambda *a, **k: _FakeClient(payload))
    return _factory


# ── helper ───────────────────────────────────────────────────────────────────

def test_strip_html_unescapes_and_drops_tags():
    assert _strip_html("&lt;p&gt;Build &amp; ship &lt;b&gt;it&lt;/b&gt;.&lt;/p&gt;") == "Build & ship it ."
    assert _strip_html(None) is None
    assert _strip_html("") is None


# ── Greenhouse ───────────────────────────────────────────────────────────────

def test_greenhouse_maps_fields_and_filters_by_relevance(_patch_httpx):
    _patch_httpx(_GH)
    jobs = GreenhouseScraper(["acme"], relevant_tokens=["engineer"]).scrape()
    assert len(jobs) == 1                            # forklift dropped by relevance
    j = jobs[0]
    assert j.source == JobSource.GREENHOUSE
    assert j.url == "https://job-boards.greenhouse.io/acme/jobs/1"
    assert j.title == "Software Engineer"
    assert j.company == "Acme"
    assert j.location == "Remote"
    assert "Build & ship things" in (j.description or "")   # HTML stripped
    assert j.posted_at is not None and j.posted_at.year == 2026


def test_greenhouse_no_relevance_keeps_all(_patch_httpx):
    _patch_httpx(_GH)
    jobs = GreenhouseScraper(["acme"]).scrape()
    assert len(jobs) == 2


# ── Lever ────────────────────────────────────────────────────────────────────

def test_lever_maps_fields_and_filters(_patch_httpx):
    _patch_httpx(_LEVER)
    jobs = LeverScraper(["acme"], relevant_tokens=["engineer"]).scrape()
    assert len(jobs) == 1                            # AE dropped by relevance
    j = jobs[0]
    assert j.source == JobSource.LEVER
    assert j.url == "https://jobs.lever.co/acme/abc"
    assert j.title == "Software Engineer"
    assert j.company == "acme"                       # company = slug
    assert j.location == "Austin, TX"
    assert j.description == "Write code."
    assert j.posted_at is not None and j.posted_at.tzinfo == timezone.utc


# ── graceful failure ─────────────────────────────────────────────────────────

def test_board_fetch_failure_is_skipped(monkeypatch):
    class _BoomClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url):
            raise httpx.ConnectError("boom", request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _BoomClient())
    assert GreenhouseScraper(["acme"]).scrape() == []   # no crash, empty result


# ── factory ──────────────────────────────────────────────────────────────────

def test_build_ats_scrapers_reads_config():
    cfg = {
        "greenhouse": {"enabled": True, "companies": ["acme"]},
        "lever": {"enabled": True, "companies": ["acme"]},
    }
    scrapers = build_ats_scrapers(["Software Engineer"], cfg)
    assert {type(s).__name__ for s in scrapers} == {"GreenhouseScraper", "LeverScraper"}


def test_build_ats_scrapers_empty_when_unconfigured():
    assert build_ats_scrapers(["Engineer"], {}) == []
    assert build_ats_scrapers(["Engineer"], {"greenhouse": {"companies": []}}) == []
    # disabled flag respected
    assert build_ats_scrapers(["Engineer"],
                              {"lever": {"enabled": False, "companies": ["acme"]}}) == []
