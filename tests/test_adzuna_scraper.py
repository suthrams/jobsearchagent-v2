# tests/test_adzuna_scraper.py
# ─────────────────────────────────────────────────────────────────────────────
# Tests for scrapers/adzuna.py.
#
# Bugs these catch:
#   - Single-location bug: `location: str` in config only searched Atlanta,
#     ignoring Houston, Newark, Seattle defined in search.locations.
#   - search.titles not being used (scrapers.adzuna.keywords was a dead copy).
#   - Ensuring all configured locations are searched, not just the first.
# ─────────────────────────────────────────────────────────────────────────────

import pytest

from models.config_schema import AdzunaConfig
from scrapers.adzuna import AdzunaScraper

TITLES = ["software architect", "principal engineer"]

def make_config(**overrides) -> AdzunaConfig:
    defaults = dict(
        enabled=True,
        country="us",
        locations=["Atlanta, GA", "Houston, TX", "Newark, NJ", "Seattle, WA"],
        radius_km=120,
        results_per_page=10,
        remote_keywords=["software architect"],
    )
    defaults.update(overrides)
    return AdzunaConfig(**defaults)


@pytest.fixture
def scraper(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "test_id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test_key")
    return AdzunaScraper(make_config(), titles=TITLES)


# ─── search.titles drives local searches ──────────────────────────────────────

def test_search_titles_used_for_local_search(monkeypatch):
    """
    Regression: scrapers.adzuna.keywords was a separate, dead list.
    Now search.titles is the single source of truth — passed as `titles` arg.
    """
    monkeypatch.setenv("ADZUNA_APP_ID", "test_id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test_key")

    custom_titles = ["iot architect", "head of engineering"]
    scraper = AdzunaScraper(make_config(), titles=custom_titles)

    calls = []
    scraper._fetch_jobs = lambda kw, location="": calls.append((kw, location)) or []
    scraper.scrape()

    searched_keywords = {kw for kw, loc in calls if loc}
    assert searched_keywords == set(custom_titles), \
        f"Expected scraper to use provided titles, got: {searched_keywords}"


# ─── Location coverage ────────────────────────────────────────────────────────

def test_all_locations_are_searched(scraper):
    """
    Regression: with `location: str` (old schema), only Atlanta was searched.
    Now `locations: list[str]` — every configured location must be used.
    """
    calls = []
    scraper._fetch_jobs = lambda kw, location="": calls.append((kw, location)) or []
    scraper.scrape()

    searched_locations = {loc for _, loc in calls if loc}
    expected = {"Atlanta, GA", "Houston, TX", "Newark, NJ", "Seattle, WA"}
    missing = expected - searched_locations
    assert not missing, f"These locations were never searched: {missing}"


def test_each_title_searched_in_each_location(scraper):
    """Every title × location combination must be tried."""
    calls = []
    scraper._fetch_jobs = lambda kw, location="": calls.append((kw, location)) or []
    scraper.scrape()

    local_calls = [(kw, loc) for kw, loc in calls if loc]
    for title in scraper.titles:
        for loc in scraper.config.locations:
            assert (title, loc) in local_calls, \
                f"Missing local search for title='{title}' location='{loc}'"


def test_remote_keywords_searched_without_location(scraper):
    """Remote keywords must be searched with an empty location string."""
    calls = []
    scraper._fetch_jobs = lambda kw, location="": calls.append((kw, location)) or []
    scraper.scrape()

    remote_calls = [kw for kw, loc in calls if not loc]
    assert len(remote_calls) == len(scraper.config.remote_keywords), \
        "Each remote keyword should produce exactly one call with no location"


def test_disabled_scraper_returns_empty(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "test_id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test_key")
    scraper = AdzunaScraper(make_config(enabled=False), titles=TITLES)
    assert scraper.scrape() == []


def test_deduplication_across_locations(scraper):
    """Same URL returned by two different location searches must appear only once."""
    from models.job import Job, JobSource
    from datetime import datetime

    shared_job = Job(
        url="https://example.com/job/1",
        source=JobSource.ADZUNA,
        title="Software Architect",
        company="Acme",
        found_at=datetime.utcnow(),
    )

    scraper._fetch_jobs = lambda kw, location="": [shared_job]
    results = scraper.scrape()

    assert len(results) == 1, \
        f"Expected 1 deduplicated job, got {len(results)}"


# ─── Title filtering ──────────────────────────────────────────────────────────

def test_irrelevant_title_dropped(scraper):
    assert not scraper._is_relevant_title("Leasing Manager - Apartments")
    assert not scraper._is_relevant_title("Property Manager")
    assert not scraper._is_relevant_title("Electrical Engineer")


def test_relevant_title_kept(scraper):
    assert scraper._is_relevant_title("Software Architect")
    assert scraper._is_relevant_title("Director of Engineering")
    assert scraper._is_relevant_title("VP of Engineering")
