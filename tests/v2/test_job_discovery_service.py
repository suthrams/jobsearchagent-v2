"""Tests for JobDiscoveryService — normalization, deduplication, discovery."""
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.job_posting import JobPosting, JobSource, WorkMode
from app.services.job_discovery_service import JobDiscoveryService


def _mock_repo(url_exists=False, url_excluded=False, url_scored_by_user=False):
    """Mock JobRepository. The dedup path uses `url_excluded` and
    `url_scored_by_user` after the 2026-05-29 fix (the old `url_exists`
    catch-all was retired because it accidentally dropped multi-profile +
    multi-run re-discovery). `url_exists` is retained as a default-False
    so legacy tests that still pass it keep working."""
    repo = MagicMock()
    repo.url_exists.return_value = url_exists
    repo.url_excluded.return_value = url_excluded
    repo.url_scored_by_user.return_value = url_scored_by_user
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
    posting = svc.normalize(_mock_v1_job(source="monster"), "wf-001")
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


def test_normalize_canonicalizes_adzuna_url():
    """Integration: normalize() must strip the rotating Adzuna `?se=`
    tracking token via the URL canonicalizer so per-URL dedup catches
    re-fetches. Regression for the 2026-05-29 'same job over and over'
    bug on the cyber-grad profile."""
    svc = _svc()
    v1_job = _mock_v1_job(url="https://www.adzuna.com/land/ad/5690461826?se=token1&utm_medium=api")
    posting = svc.normalize(v1_job, "wf-001")
    assert posting.url == "https://www.adzuna.com/land/ad/5690461826"


def test_normalize_leaves_non_adzuna_url_unchanged():
    """Sanity: only Adzuna URLs get canonicalized. LinkedIn and others
    pass through (until proven otherwise)."""
    svc = _svc()
    v1_job = _mock_v1_job(url="https://www.linkedin.com/jobs/view/1?trackingId=x")
    posting = svc.normalize(v1_job, "wf-001")
    assert posting.url == "https://www.linkedin.com/jobs/view/1?trackingId=x"


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


def test_deduplicate_drops_excluded_urls():
    """ADR-057 invariant: URLs flagged excluded in the jobs table are
    never re-surfaced. After the 2026-05-29 dedup-narrowing fix, this is
    the ONLY global dedup check - URLs that merely exist in the DB but
    are NOT excluded must be re-discoverable (see
    test_deduplicate_does_not_drop_merely_persisted_urls below)."""
    repo = _mock_repo(url_excluded=True)
    svc = _svc(repo=repo)
    jobs = [svc.normalize(_mock_v1_job(), "wf")]
    result = svc.deduplicate(jobs)
    assert len(result) == 0


def test_deduplicate_does_not_drop_merely_persisted_urls():
    """Regression test for the 2026-05-29 'security profile finds the
    same job over and over' bug. A URL that exists in `jobs` but is NOT
    flagged excluded must be re-discoverable. Before the fix, the
    cyber-grad profile saw 1 job per run for three runs in a row because
    every newly-scraped URL had already been persisted by the previous
    run and was silently filtered out."""
    repo = _mock_repo(url_exists=True, url_excluded=False)
    svc = _svc(repo=repo)
    jobs = [svc.normalize(_mock_v1_job(), "wf")]
    result = svc.deduplicate(jobs)
    assert len(result) == 1, \
        "persisted-but-not-excluded URLs must remain discoverable"


def test_deduplicate_drops_urls_already_scored_by_this_user():
    """Per-user dedup: if THIS user already scored this URL in a prior
    workflow run, skip it (cost saver)."""
    repo = _mock_repo(url_scored_by_user=True)
    svc = _svc(repo=repo)
    jobs = [svc.normalize(_mock_v1_job(), "wf")]
    result = svc.deduplicate(jobs, user_id="user-A")
    assert len(result) == 0


def test_deduplicate_per_user_check_skipped_without_user_id():
    """Backward compat: callers that don't pass user_id still get the
    excluded check but not the per-user-already-scored check."""
    repo = _mock_repo(url_scored_by_user=True)
    svc = _svc(repo=repo)
    jobs = [svc.normalize(_mock_v1_job(), "wf")]
    result = svc.deduplicate(jobs)  # no user_id
    assert len(result) == 1
    assert not repo.url_scored_by_user.called


def test_deduplicate_per_user_does_not_drop_other_users_scores():
    """User isolation: a URL scored by User A must remain discoverable
    for User B. The mock simulates 'scored by user-A' by returning True
    only for user-A; user-B sees False."""
    repo = MagicMock()
    repo.url_excluded.return_value = False
    repo.url_scored_by_user.side_effect = lambda url, uid: uid == "user-A"
    svc = _svc(repo=repo)
    jobs = [svc.normalize(_mock_v1_job(), "wf")]
    assert len(svc.deduplicate(jobs, user_id="user-A")) == 0
    assert len(svc.deduplicate(jobs, user_id="user-B")) == 1


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
