"""ADR-080: posting-age staleness signal + opt-in max-age filter."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.schemas.job_posting import JobPosting, JobSource
from app.services.job_discovery_service import JobDiscoveryService
from app.services.posting_age_filter import (
    is_older_than,
    is_stale,
    posting_age_days,
)
from app.ui.formatting import format_posting_age
from app.workflows.nodes.discover_jobs import make_discover_jobs_node

# Fixed clock so age math is deterministic regardless of when the suite runs.
NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)


# ── helper: age math ─────────────────────────────────────────────────────────

def test_posting_age_days():
    assert posting_age_days("2026-06-01T12:00:00.000Z", now=NOW) == 3
    assert posting_age_days("2026-06-04T00:00:00Z", now=NOW) == 0
    assert posting_age_days(None, now=NOW) is None
    assert posting_age_days("not a date", now=NOW) is None
    # future-dated clamps to 0, never negative
    assert posting_age_days("2099-01-01T00:00:00Z", now=NOW) == 0


def test_is_older_than_keeps_unknown_and_respects_off():
    assert is_older_than("2026-01-01T00:00:00Z", 30, now=NOW) is True    # ~5 months -> drop
    assert is_older_than("2026-06-01T00:00:00Z", 30, now=NOW) is False   # 3 days -> keep
    assert is_older_than(None, 30, now=NOW) is False                     # unknown -> keep
    assert is_older_than("garbage", 30, now=NOW) is False                # unparseable -> keep
    assert is_older_than("2026-01-01T00:00:00Z", 0, now=NOW) is False    # 0 disables
    assert is_older_than("2026-01-01T00:00:00Z", None, now=NOW) is False # None disables


def test_is_stale():
    assert is_stale("2026-01-01T00:00:00Z", now=NOW) is True
    assert is_stale("2026-06-01T00:00:00Z", now=NOW) is False
    assert is_stale(None, now=NOW) is False


# ── discovery applies the cap (keep-when-unknown) ────────────────────────────

def _posting(jid, posted_at):
    return JobPosting(job_id=jid, workflow_id="wf", source=JobSource.ADZUNA,
                      title="Security Analyst", company="Acme", location="Remote",
                      url=f"http://x/{jid}", description="role",
                      found_at="2026-06-04T00:00:00Z", posted_at=posted_at)


def _repo_no_dedup():
    repo = MagicMock()
    repo.url_excluded.return_value = False
    repo.url_scored_by_user.return_value = False
    return repo


def _svc():
    svc = JobDiscoveryService(_repo_no_dedup(), {"search": {"max_jobs": 50}}, scrapers=[])
    svc.normalize = lambda job, wf: job  # scrapers already return JobPosting here
    return svc


# Old/fresh chosen as far-past / far-future so the result is stable vs the real
# clock the service uses internally (the helper unit tests cover the math).
def _POSTINGS():
    return [
        _posting("old", "2020-01-01T00:00:00Z"),     # very old -> drop
        _posting("fresh", "2099-01-01T00:00:00Z"),   # future -> age 0 -> keep
        _posting("nodate", None),                    # unknown -> keep
    ]


def test_discover_drops_stale_keeps_unknown():
    svc = _svc()

    class _S:
        def scrape(self_): return _POSTINGS()

    out = svc.discover("wf", {}, extra_scrapers=[_S()], max_posting_age_days=30)
    assert {p.job_id for p in out} == {"fresh", "nodate"}

    # Off by default: nothing dropped on age.
    out_off = svc.discover("wf", {}, extra_scrapers=[_S()])
    assert {p.job_id for p in out_off} == {"old", "fresh", "nodate"}


def test_age_filter_reports_stat():
    svc = _svc()

    class _S:
        def scrape(self_): return _POSTINGS()

    _out, stats = svc.discover_with_stats("wf", {}, extra_scrapers=[_S()],
                                          max_posting_age_days=30)
    assert stats["age_filter_dropped"] == 1


# ── node threads the config knob through ─────────────────────────────────────

def test_node_passes_max_posting_age_days():
    captured: dict = {}
    svc = MagicMock(spec=JobDiscoveryService)

    def _discover_with_stats(workflow_id, search_criteria, extra_scrapers=None,
                             skip_builtin_adzuna=False, max_years_experience=None,
                             min_years_experience=None, max_posting_age_days=None,
                             drop_dead_links=False, user_id=None):
        captured["age"] = max_posting_age_days
        return [], {}

    svc.discover_with_stats.side_effect = _discover_with_stats

    node = make_discover_jobs_node(svc, MagicMock(), MagicMock())
    node({
        "workflow_id": "wf",
        "search_criteria": {"roles": ["Security Analyst"]},
        "effective_config": {"search": {"max_posting_age_days": 45}},
    })
    assert captured["age"] == 45


# ── UI formatter ─────────────────────────────────────────────────────────────

def test_format_posting_age():
    assert format_posting_age("2026-06-01T00:00:00Z", now=NOW) == "Posted 3 days ago"
    assert format_posting_age("2026-06-03T00:00:00Z", now=NOW) == "Posted 1 day ago"
    assert format_posting_age("2026-06-04T00:00:00Z", now=NOW) == "Posted today"
    assert format_posting_age(None, now=NOW) == ""
    assert format_posting_age("garbage", now=NOW) == ""
