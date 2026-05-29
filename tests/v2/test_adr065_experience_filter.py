"""ADR-065: years-of-experience cap + senior exclusion in discovery."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.experience_filter import (
    min_required_years, max_required_years, exceeds_cap, below_floor,
)
from app.services.job_discovery_service import JobDiscoveryService
from app.schemas.job_posting import JobPosting, JobSource
from app.workflows.nodes.discover_jobs import make_discover_jobs_node


# ── min_required_years ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("5+ years of experience required", 5),
    ("3-5 years experience", 3),
    ("3 to 7 years", 3),
    ("minimum of 2 years of experience", 2),
    ("at least 4 yrs", 4),
    ("Entry level role, new grad welcome", 0),
    ("No prior experience required", 0),
    ("We value collaboration and curiosity.", None),   # no signal
    (None, None),
    ("Requires 8+ years; 3+ years preferred in security", 3),  # min across matches
])
def test_min_required_years(text, expected):
    assert min_required_years(text) == expected


def test_exceeds_cap_keeps_silent_and_low():
    assert exceeds_cap("7+ years required", 2) is True
    assert exceeds_cap("2 years experience", 2) is False
    assert exceeds_cap("entry level", 2) is False
    assert exceeds_cap(None, 2) is False           # silent JD kept
    assert exceeds_cap("great team", 2) is False   # undetectable kept


def test_max_required_years():
    assert max_required_years("2+ years required, 5+ years preferred") == 5
    assert max_required_years("entry level") == 0
    assert max_required_years("no numbers here") is None


def test_below_floor_uses_highest_bar_keeps_silent():
    assert below_floor("2 years experience", 5) is True                  # senior bar 2 < 5 -> drop
    assert below_floor("2+ years required, 5+ years preferred", 5) is False  # senior bar 5 -> kept
    assert below_floor("7+ years", 5) is False                           # above floor
    assert below_floor("entry level", 5) is True                         # 0 < 5 -> drop junior
    assert below_floor(None, 5) is False                                 # silent JD kept
    assert below_floor("collaborative team", 5) is False                 # undetectable kept


# ── discover() applies the cap ────────────────────────────────────────────────

def _posting(jid, desc):
    return JobPosting(job_id=jid, workflow_id="wf", source=JobSource.ADZUNA,
                      title="Security Analyst", company="Acme", location="Remote",
                      url=f"http://x/{jid}", description=desc,
                      found_at="2026-05-26T00:00:00Z")


def _repo_no_dedup():
    """MagicMock JobRepository with all dedup checks disabled - so these
    tests exercise the experience filter in isolation."""
    repo = MagicMock()
    repo.url_excluded.return_value = False
    repo.url_scored_by_user.return_value = False
    return repo


def test_discover_drops_postings_over_cap():
    svc = JobDiscoveryService(_repo_no_dedup(), {"search": {"max_jobs": 50}}, scrapers=[])
    svc.normalize = lambda job, wf: job  # scrapers already return JobPosting here
    postings = [
        _posting("a", "5+ years required"),       # drop
        _posting("b", "2 years experience"),       # keep
        _posting("c", "entry level"),              # keep
        _posting("d", "collaborative team"),       # keep (silent)
    ]

    class _S:
        def scrape(self_): return postings
    out = svc.discover("wf", {}, extra_scrapers=[_S()], max_years_experience=2)
    ids = {p.job_id for p in out}
    assert ids == {"b", "c", "d"}

    out_nocap = svc.discover("wf", {}, extra_scrapers=[_S()])
    assert {p.job_id for p in out_nocap} == {"a", "b", "c", "d"}


def test_discover_drops_postings_below_floor():
    svc = JobDiscoveryService(_repo_no_dedup(), {"search": {"max_jobs": 50}}, scrapers=[])
    svc.normalize = lambda job, wf: job
    postings = [
        _posting("a", "2 years experience"),       # below floor 5 -> drop
        _posting("b", "7+ years required"),         # keep
        _posting("c", "2+ years required, 5+ years preferred"),  # senior bar 5 -> keep
        _posting("d", "collaborative team"),        # silent -> keep
    ]

    class _S:
        def scrape(self_): return postings
    out = svc.discover("wf", {}, extra_scrapers=[_S()], min_years_experience=5)
    assert {p.job_id for p in out} == {"b", "c", "d"}


# ── node reads effective_config + passes cap / exclude_senior ─────────────────

def test_node_passes_cap_and_exclude_senior_from_effective_config():
    cap: dict = {}
    svc = MagicMock(spec=JobDiscoveryService)
    def _discover_with_stats(workflow_id, search_criteria, extra_scrapers=None,
                             skip_builtin_adzuna=False, max_years_experience=None,
                             min_years_experience=None, user_id=None):
        cap["max_years"] = max_years_experience
        return [], {}
    svc.discover_with_stats.side_effect = _discover_with_stats

    factory_calls = []
    factory = lambda roles, locations, exclude_senior=False: (
        factory_calls.append(exclude_senior) or object())

    node = make_discover_jobs_node(svc, MagicMock(), MagicMock(),
                                   adzuna_scraper_factory=factory)
    node({
        "workflow_id": "wf",
        "search_criteria": {"roles": ["Security Analyst"], "locations": ["Remote"]},
        "effective_config": {"search": {"max_years_experience": 2, "exclude_senior": True}},
    })
    assert cap["max_years"] == 2
    assert factory_calls == [True]
