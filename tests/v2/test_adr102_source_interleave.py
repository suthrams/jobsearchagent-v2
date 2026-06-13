"""ADR-102: source-fair round-robin interleave before the discovery caps.

The unit tests pin the interleave helper; the funnel test is the regression guard
for the validation-run starvation (a later-appended ATS source getting truncated
out before it is ever scored).
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app.services.source_interleave import interleave_by_source, source_mix
from app.services.job_discovery_service import JobDiscoveryService


# ── interleave_by_source ────────────────────────────────────────────────────

def _k(s):
    """Group key for plain strings (the tests use strings as 'sources')."""
    return s


def test_round_robins_three_sources_preserving_within_source_order():
    # 4 'a', 2 'b', 1 'c' in append order -> round-robin, within-source order kept.
    items = ["a1", "a2", "a3", "a4", "b1", "b2", "c1"]
    out = interleave_by_source(items, key=lambda x: x[0])
    assert out == ["a1", "b1", "c1", "a2", "b2", "a3", "a4"]


def test_identity_on_empty_and_single():
    assert interleave_by_source([]) == []
    assert interleave_by_source(["x"], key=_k) == ["x"]


def test_identity_on_single_source():
    items = ["a1", "a2", "a3"]
    assert interleave_by_source(items, key=lambda x: x[0]) == items


def test_is_deterministic_stable():
    items = ["a1", "b1", "a2", "c1", "a3", "b2"]
    once = interleave_by_source(items, key=lambda x: x[0])
    twice = interleave_by_source(list(items), key=lambda x: x[0])
    assert once == twice


def test_never_drops_or_adds():
    items = [f"{s}{i}" for s in "abcde" for i in range(3)]
    out = interleave_by_source(items, key=lambda x: x[0])
    assert sorted(out) == sorted(items)


def test_source_mix_counts():
    items = ["a1", "a2", "b1"]
    assert source_mix(items, key=lambda x: x[0]) == {"a": 2, "b": 1}


def test_source_mix_unwraps_enum_value():
    src = MagicMock()
    src.value = "greenhouse"
    item = MagicMock()
    item.source = src
    assert source_mix([item]) == {"greenhouse": 1}


# ── funnel regression: ATS survives a tight cap (the validation-run bug) ─────

def _scraper(jobs):
    s = MagicMock()
    s.scrape.return_value = jobs
    return s


def _job(url, source, title="SOC Analyst", company="Co"):
    j = MagicMock()
    j.url = url
    j.title = title
    j.company = company
    j.source = source
    j.work_mode = None
    j.location = "Remote"
    j.description = "Entry-level SOC analyst role."
    j.salary = None
    j.found_at = None
    j.posted_at = None
    return j


def test_ats_survives_tight_cap_after_interleave():
    """Adzuna-heavy + a few ATS jobs, Adzuna scraper FIRST, cap below the Adzuna
    count. Pre-ADR-102 the ATS jobs were all truncated (append order); now the
    interleave guarantees them early slots so they survive the cap."""
    repo = MagicMock()
    repo.url_exists.return_value = False
    repo.url_excluded.return_value = False
    repo.url_scored_by_user.return_value = False

    adzuna_jobs = [_job(f"https://adz/{i}", "adzuna") for i in range(10)]
    ats_jobs = [_job(f"https://gh/{i}", "greenhouse") for i in range(3)]

    svc = JobDiscoveryService(
        job_repository=repo,
        config={"search": {"max_jobs": 6}},
        # Adzuna scraper listed FIRST (mirrors the always-first built-in Adzuna).
        scrapers=[_scraper(adzuna_jobs), _scraper(ats_jobs)],
    )
    postings, stats = svc.discover_with_stats("wf-1", {"roles": ["SOC Analyst"]})

    assert len(postings) == 6
    sources = [str(getattr(p.source, "value", p.source)) for p in postings]
    # All 3 greenhouse jobs survive the cap-to-6 (would be 0 without interleave).
    assert sources.count("greenhouse") == 3
    assert sources.count("adzuna") == 3
    assert stats["source_mix"] == {"adzuna": 3, "greenhouse": 3}
