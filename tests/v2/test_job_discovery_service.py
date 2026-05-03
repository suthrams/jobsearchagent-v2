"""Tests for JobDiscoveryService — normalization, deduplication, discovery."""
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.job_posting import JobPosting, JobSource, WorkMode
from app.services.job_discovery_service import JobDiscoveryService


def _mock_repo(url_exists=False):
    repo = MagicMock()
    repo.url_exists.return_value = url_exists
    return repo


def _mock_v1_job(
    url="https://example.com/job/1",
    title="Staff Engineer",
    company="Acme Corp",
    source="linkedin",
    work_mode=None,
    location="Remote",
    description="We need a Staff Engineer.",
    salary=None,
    found_at=None,
    posted_at=None,
):
    job = MagicMock()
    job.url = url
    job.title = title
    job.company = company
    job.source = source
    job.work_mode = work_mode
    job.location = location
    job.description = description
    job.salary = salary
    job.found_at = found_at
    job.posted_at = posted_at
    return job


def _svc(repo=None, max_jobs=20, scrapers=None):
    return JobDiscoveryService(
        job_repository=repo or _mock_repo(),
        config={"search": {"max_jobs": max_jobs}},
        scrapers=scrapers or [],
    )


# ── normalize() ───────────────────────────────────────────────────────────────

def test_normalize_maps_source():
    svc = _svc()
    posting = svc.normalize(_mock_v1_job(source="linkedin"), "wf-001")
    assert posting.source == JobSource.LINKEDIN


def test_normalize_adzuna_source():
    svc = _svc()
    posting = svc.normalize(_mock_v1_job(source="adzuna"), "wf-001")
    assert posting.source == JobSource.ADZUNA


def test_normalize_unknown_source_maps_to_manual():
    svc = _svc()
    posting = svc.normalize(_mock_v1_job(source="glassdoor"), "wf-001")
    assert posting.source == JobSource.MANUAL


def test_normalize_assigns_uuid_job_id():
    svc = _svc()
    p1 = svc.normalize(_mock_v1_job(), "wf-001")
    p2 = svc.normalize(_mock_v1_job(), "wf-001")
    assert p1.job_id != p2.job_id
    assert len(p1.job_id) == 36  # UUID format


def test_normalize_none_work_mode_maps_to_unknown():
    svc = _svc()
    posting = svc.normalize(_mock_v1_job(work_mode=None), "wf-001")
    assert posting.work_mode == WorkMode.UNKNOWN


def test_normalize_remote_work_mode():
    svc = _svc()
    posting = svc.normalize(_mock_v1_job(work_mode="remote"), "wf-001")
    assert posting.work_mode == WorkMode.REMOTE


def test_normalize_sets_workflow_id():
    svc = _svc()
    posting = svc.normalize(_mock_v1_job(), "wf-test-123")
    assert posting.workflow_id == "wf-test-123"


def test_normalize_returns_job_posting():
    svc = _svc()
    posting = svc.normalize(_mock_v1_job(), "wf-001")
    assert isinstance(posting, JobPosting)


def test_normalize_with_salary():
    salary = MagicMock()
    salary.min = 150000
    salary.max = 200000
    salary.currency = "USD"
    svc = _svc()
    posting = svc.normalize(_mock_v1_job(salary=salary), "wf-001")
    assert posting.salary is not None
    assert posting.salary.min_amount == 150000


# ── deduplicate() ─────────────────────────────────────────────────────────────

def test_deduplicate_removes_batch_duplicates():
    repo = _mock_repo(url_exists=False)
    svc = _svc(repo=repo)
    jobs = [
        svc.normalize(_mock_v1_job(url="https://example.com/1"), "wf"),
        svc.normalize(_mock_v1_job(url="https://example.com/1"), "wf"),  # duplicate
        svc.normalize(_mock_v1_job(url="https://example.com/2"), "wf"),
    ]
    result = svc.deduplicate(jobs)
    assert len(result) == 2


def test_deduplicate_removes_db_existing():
    repo = _mock_repo(url_exists=True)  # all URLs already in DB
    svc = _svc(repo=repo)
    jobs = [svc.normalize(_mock_v1_job(), "wf")]
    result = svc.deduplicate(jobs)
    assert len(result) == 0


def test_deduplicate_preserves_unique():
    repo = _mock_repo(url_exists=False)
    svc = _svc(repo=repo)
    jobs = [
        svc.normalize(_mock_v1_job(url="https://example.com/1"), "wf"),
        svc.normalize(_mock_v1_job(url="https://example.com/2"), "wf"),
    ]
    result = svc.deduplicate(jobs)
    assert len(result) == 2


# ── discover() ────────────────────────────────────────────────────────────────

def test_discover_caps_results():
    scraper = MagicMock()
    scraper.scrape.return_value = [_mock_v1_job(url=f"https://example.com/{i}") for i in range(30)]
    svc = _svc(max_jobs=5, scrapers=[scraper])
    results = svc.discover("wf-001", {})
    assert len(results) <= 5


def test_discover_continues_on_scraper_failure():
    bad_scraper = MagicMock()
    bad_scraper.scrape.side_effect = RuntimeError("connection refused")
    good_scraper = MagicMock()
    good_scraper.scrape.return_value = [_mock_v1_job()]
    svc = _svc(scrapers=[bad_scraper, good_scraper])
    results = svc.discover("wf-001", {})
    assert len(results) == 1


def test_discover_filters_excluded_titles():
    scraper = MagicMock()
    scraper.scrape.return_value = [
        _mock_v1_job(title="Sales Engineer"),   # excluded
        _mock_v1_job(title="Staff Engineer", url="https://example.com/2"),  # kept
    ]
    svc = _svc(scrapers=[scraper])
    results = svc.discover("wf-001", {})
    assert len(results) == 1
    assert results[0].title == "Staff Engineer"


def test_discover_no_scrapers_returns_empty():
    svc = _svc(scrapers=[])
    results = svc.discover("wf-001", {})
    assert results == []


def test_discover_extra_scrapers_run_first_so_user_urls_survive_cap():
    """Regression: when both built-in (Adzuna) and per-run extras (CustomUrlScraper)
    produce more jobs than max_jobs, the user's pasted URLs must survive the cap.
    Achieved by processing extras BEFORE built-ins in JobDiscoveryService.discover.
    """
    builtin = MagicMock()
    builtin.scrape.return_value = [
        _mock_v1_job(url=f"https://adzuna.example/{i}") for i in range(10)
    ]
    extra = MagicMock()
    extra.scrape.return_value = [
        _mock_v1_job(url="https://user-supplied.example/a"),
        _mock_v1_job(url="https://user-supplied.example/b"),
    ]

    svc = _svc(max_jobs=10, scrapers=[builtin])
    results = svc.discover("wf-001", {}, extra_scrapers=[extra])

    urls = [p.url for p in results]
    assert "https://user-supplied.example/a" in urls, \
        "user-supplied URL was dropped by the max_jobs cap"
    assert "https://user-supplied.example/b" in urls, \
        "user-supplied URL was dropped by the max_jobs cap"
    assert len(results) == 10


def test_discover_extra_scrapers_run_when_no_builtins():
    extra = MagicMock()
    extra.scrape.return_value = [_mock_v1_job(url="https://x.example/a")]
    svc = _svc(scrapers=[])
    results = svc.discover("wf-001", {}, extra_scrapers=[extra])
    assert len(results) == 1
    assert results[0].url == "https://x.example/a"
