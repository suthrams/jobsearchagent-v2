"""ADR-103: profile-derived location/country filter.

Unit tests pin the country resolver + the filter; the funnel test is the
regression guard for the validation-run leak (a US profile getting Ireland/UK
roles from an ATS global board).
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app.services.location_filter import (
    country_of,
    derive_allowed_countries,
    filter_by_location,
)
from app.services.job_discovery_service import JobDiscoveryService


# ── country_of ──────────────────────────────────────────────────────────────

def test_us_forms_resolve_to_us():
    for s in [
        "Atlanta, GA", "Maryland, US", "Remote US", "West Virginia",
        "United States of America", "Oakton, VA", "New York, NY", "USA",
    ]:
        assert country_of(s) == "US", s


def test_foreign_forms_resolve_to_iso():
    assert country_of("Ireland") == "IE"
    assert country_of("SOC Support Specialist- UK".split("-")[-1]) == "GB"  # " UK"
    assert country_of("United Kingdom") == "GB"
    assert country_of("London, UK") == "GB"
    assert country_of("Toronto, Canada") == "CA"
    assert country_of("Bangalore, India") == "IN"


def test_ambiguous_resolves_to_none_kept():
    # No confident country token -> None (the keep signal).
    assert country_of("Oakton, Fairfax County") is None
    assert country_of("Remote") is None
    assert country_of("") is None
    assert country_of(None) is None


def test_no_substring_false_positive():
    # "Houston" contains the letters "us" but must NOT resolve to US by substring;
    # the ", TX" state code is what (correctly) makes it US.
    assert country_of("Houston") is None
    assert country_of("Houston, TX") == "US"


def test_explicit_foreign_country_beats_us_state_token():
    # "Dublin, OH" is US (Ohio); "Dublin, Ireland" is IE - the explicit country wins.
    assert country_of("Dublin, OH") == "US"
    assert country_of("Dublin, Ireland") == "IE"


# ── derive_allowed_countries ────────────────────────────────────────────────

def test_derive_from_profile_locations():
    locs = ["Atlanta, GA", "West Virginia", "New Jersey, NJ", "Remote"]
    assert derive_allowed_countries(locs) == {"US"}


def test_derive_empty_when_unresolvable():
    assert derive_allowed_countries(["Remote", "Anywhere"]) == set()
    assert derive_allowed_countries(None) == set()


def test_derive_multi_country_profile():
    assert derive_allowed_countries(["London, UK", "Toronto, Canada"]) == {"GB", "CA"}


# ── filter_by_location ──────────────────────────────────────────────────────

def _p(location, title="SOC Analyst", company="Co", url="https://x/1"):
    p = MagicMock()
    p.location = location
    p.title = title
    p.company = company
    p.url = url
    return p


def test_drops_foreign_keeps_us_and_ambiguous():
    postings = [
        _p("Remote US"), _p("Ireland"), _p("United Kingdom"),
        _p("Oakton, Fairfax County"), _p("Maryland, US"),
    ]
    kept, dropped, samples = filter_by_location(postings, {"US"})
    locs = [p.location for p in kept]
    assert "Ireland" not in locs and "United Kingdom" not in locs
    assert "Remote US" in locs and "Maryland, US" in locs
    assert "Oakton, Fairfax County" in locs  # ambiguous -> kept
    assert dropped == 2
    assert {s["country"] for s in samples} == {"IE", "GB"}


def test_empty_allowed_keeps_everything():
    postings = [_p("Ireland"), _p("United Kingdom")]
    kept, dropped, _ = filter_by_location(postings, set())
    assert len(kept) == 2 and dropped == 0


# ── funnel regression: ATS global-board leak is dropped ─────────────────────

def _scraper(jobs):
    s = MagicMock()
    s.scrape.return_value = jobs
    return s


def _job(url, location, source="greenhouse", title="SOC Support Specialist"):
    j = MagicMock()
    j.url = url
    j.title = title
    j.company = "Huntress"
    j.source = source
    j.work_mode = None
    j.location = location
    j.description = "SOC role."
    j.salary = None
    j.found_at = None
    j.posted_at = None
    return j


def test_funnel_drops_foreign_ats_jobs_for_us_profile():
    repo = MagicMock()
    repo.url_exists.return_value = False
    repo.url_excluded.return_value = False
    repo.url_scored_by_user.return_value = False

    jobs = [
        _job("https://gh/1", "Remote US"),
        _job("https://gh/2", "Ireland"),
        _job("https://gh/3", "United Kingdom"),
        _job("https://gh/4", "United States of America"),
    ]
    svc = JobDiscoveryService(
        job_repository=repo,
        config={"search": {"max_jobs": 50}},
        scrapers=[_scraper(jobs)],
    )
    postings, stats = svc.discover_with_stats(
        "wf-1",
        {"roles": ["SOC Analyst"], "locations": ["Atlanta, GA", "Remote"]},
    )
    locs = [p.location for p in postings]
    assert "Ireland" not in locs and "United Kingdom" not in locs
    assert "Remote US" in locs and "United States of America" in locs
    assert stats["location_dropped"] == 2


def test_funnel_no_location_scope_keeps_all():
    """A profile whose locations don't resolve to a country -> filter is a no-op."""
    repo = MagicMock()
    repo.url_exists.return_value = False
    repo.url_excluded.return_value = False
    repo.url_scored_by_user.return_value = False
    jobs = [_job("https://gh/2", "Ireland"), _job("https://gh/3", "United Kingdom")]
    svc = JobDiscoveryService(
        job_repository=repo, config={"search": {"max_jobs": 50}},
        scrapers=[_scraper(jobs)],
    )
    postings, stats = svc.discover_with_stats(
        "wf-1", {"roles": ["SOC Analyst"], "locations": ["Remote"]},
    )
    assert len(postings) == 2 and stats["location_dropped"] == 0
