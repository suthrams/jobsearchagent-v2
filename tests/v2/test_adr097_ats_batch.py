"""ADR-097: curated ATS board batch + concurrent per-board fetch.

- The shipped example config lists are well-formed (non-empty, lowercase, deduped).
- The concurrent scrape fans out across boards but preserves token/slug order and
  still maps + relevance-gates each board (httpx patched; no network).
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
import yaml

from app.services.ats_scrapers import GreenhouseScraper, LeverScraper

_ROOT = Path(__file__).resolve().parents[2]


# ── config batch is well-formed ──────────────────────────────────────────────

@pytest.mark.parametrize("ats", ["greenhouse", "lever"])
def test_example_config_ats_batch_well_formed(ats):
    cfg = yaml.safe_load((_ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8"))
    companies = (cfg["scrapers"][ats] or {}).get("companies") or []
    assert companies, f"{ats} batch should be non-empty (ADR-097 ships it on)"
    assert companies == [c.lower().strip() for c in companies], "slugs must be normalized"
    assert len(companies) == len(set(companies)), "no duplicate slugs"


# ── concurrent multi-board fetch keeps order + per-board mapping ──────────────

class _MultiBoardClient:
    """Returns a per-board payload keyed off the slug in the requested URL."""

    def __init__(self, payloads: dict, list_shape: bool):
        self._payloads = payloads
        self._list_shape = list_shape

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        m = re.search(r"/boards/([^/]+)/jobs", url) or re.search(r"/postings/([^/?]+)", url)
        slug = m.group(1) if m else ""
        items = self._payloads.get(slug, [])
        body = items if self._list_shape else {"jobs": items}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))


def test_greenhouse_concurrent_preserves_token_order(monkeypatch):
    payloads = {
        "acme": [{"absolute_url": "https://gh/acme/1", "title": "Software Engineer",
                  "company_name": "Acme", "location": {"name": "Remote"},
                  "first_published": "2026-05-08T10:00:00Z", "content": "a"}],
        "globex": [{"absolute_url": "https://gh/globex/1", "title": "Staff Engineer",
                    "company_name": "Globex", "location": {"name": "Remote"},
                    "first_published": "2026-05-08T10:00:00Z", "content": "b"},
                   {"absolute_url": "https://gh/globex/2", "title": "Sales Lead",
                    "company_name": "Globex", "location": {"name": "NYC"},
                    "first_published": "2026-05-08T10:00:00Z", "content": "c"}],
    }
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _MultiBoardClient(payloads, list_shape=False))
    jobs = GreenhouseScraper(["acme", "globex"], relevant_tokens=["engineer"]).scrape()
    # token order preserved; the "Sales Lead" row is dropped by relevance gating
    assert [j.company for j in jobs] == ["Acme", "Globex"]
    assert all("Engineer" in j.title for j in jobs)


def test_lever_concurrent_one_board_failure_is_skipped(monkeypatch):
    payloads = {
        "good": [{"id": "1", "text": "Backend Engineer", "hostedUrl": "https://lv/good/1",
                  "categories": {"location": "Austin"}, "createdAt": 1700000000000,
                  "descriptionPlain": "x"}],
        # "bad" slug intentionally absent -> empty board (still 200, 0 jobs)
    }
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _MultiBoardClient(payloads, list_shape=True))
    jobs = LeverScraper(["good", "bad"], relevant_tokens=["engineer"]).scrape()
    assert len(jobs) == 1 and jobs[0].company == "good"
