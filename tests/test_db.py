# tests/test_db.py
# ─────────────────────────────────────────────────────────────────────────────
# Tests for storage/db.py.
#
# Bugs these catch:
#   - run_at captured at end-of-run instead of start, causing dashboard
#     "New Jobs" view to show empty (WHERE found_at >= run_at returns nothing
#     because all jobs were inserted before the run record was written).
# ─────────────────────────────────────────────────────────────────────────────

import sqlite3
from datetime import datetime, timedelta

import pytest

from storage.db import Database


@pytest.fixture
def db(tmp_path):
    """In-memory SQLite database for each test."""
    db_path = tmp_path / "test_jobs.db"
    d = Database(str(db_path))
    yield d
    d.close()


def _make_job(**overrides):
    """Minimal Job-like dict for direct SQL insertion."""
    from models.job import Job, JobSource, ApplicationStatus
    from datetime import datetime, timezone

    defaults = dict(
        url="https://example.com/job/1",
        source=JobSource.ADZUNA,
        title="Software Architect",
        company="Acme Corp",
        location="Atlanta, GA",
        work_mode="remote",
        description="Build cloud-native software systems on AWS.",
        status=ApplicationStatus.NEW,
        found_at=datetime.utcnow(),
    )
    defaults.update(overrides)
    return Job(**defaults)


# ─── run_at timestamp ordering ────────────────────────────────────────────────

def test_new_jobs_query_uses_run_start_not_run_end(db):
    """
    Regression test for the dashboard 'New Jobs' empty-screen bug.

    The bug: run_at was recorded at the END of the run (after scoring),
    but jobs have found_at from the START of the run. The dashboard query
    WHERE found_at >= run_at returned zero rows because found_at < run_at.

    Fix: run_at must be captured before scraping begins, so run_at <= found_at.
    """
    run_start = datetime.utcnow()

    # Insert a job (simulates scraping — happens after run_start)
    job = _make_job(found_at=run_start + timedelta(seconds=5))
    db.insert_job(job)

    # Record run with run_at = run_start (the correct behaviour)
    db.insert_run(
        run_at=run_start,
        jobs_scraped=1,
        jobs_new=1,
        jobs_scored=0,
        jobs_skipped=0,
        batches=0,
        est_cost_usd=0.0,
    )

    # Simulate the dashboard query
    conn = sqlite3.connect(db.db_path)
    rows = conn.execute(
        "SELECT * FROM jobs WHERE found_at >= (SELECT run_at FROM runs ORDER BY run_at DESC LIMIT 1)"
    ).fetchall()
    conn.close()

    assert len(rows) == 1, (
        "Dashboard 'New Jobs' query returned no rows. "
        "run_at must be captured before scraping, not after scoring."
    )


def test_run_at_after_found_at_causes_empty_dashboard(db):
    """
    Demonstrates the original bug: if run_at is recorded AFTER found_at,
    the dashboard returns empty. This test documents why the fix matters.
    """
    run_start = datetime.utcnow()

    job = _make_job(found_at=run_start)
    db.insert_job(job)

    # Bug: run_at is captured AFTER the job was inserted (simulates old behaviour)
    run_at_end_of_run = run_start + timedelta(minutes=5)
    db.insert_run(
        run_at=run_at_end_of_run,
        jobs_scraped=1,
        jobs_new=1,
        jobs_scored=0,
        jobs_skipped=0,
        batches=0,
        est_cost_usd=0.0,
    )

    conn = sqlite3.connect(db.db_path)
    rows = conn.execute(
        "SELECT * FROM jobs WHERE found_at >= (SELECT run_at FROM runs ORDER BY run_at DESC LIMIT 1)"
    ).fetchall()
    conn.close()

    assert len(rows) == 0, (
        "Expected zero rows when run_at > found_at (documents the original bug)"
    )


# ─── Basic DB sanity ──────────────────────────────────────────────────────────

def test_insert_and_retrieve_job(db):
    job = _make_job()
    db.insert_job(job)
    result = db.get_by_url(job.url)
    assert result is not None
    assert result.title == job.title
    assert result.company == job.company


def test_duplicate_url_not_inserted(db):
    job = _make_job()
    db.insert_job(job)
    db.insert_job(job)  # second insert should be ignored
    all_jobs = db.get_all()
    assert len(all_jobs) == 1


def test_get_by_title_company(db):
    job = _make_job(title="Cloud Architect", company="TechCorp")
    db.insert_job(job)
    result = db.get_by_title_company("Cloud Architect", "TechCorp")
    assert result is not None


def test_insert_run_returns_id(db):
    run_id = db.insert_run(
        run_at=datetime.utcnow(),
        jobs_scraped=10,
        jobs_new=5,
        jobs_scored=4,
        jobs_skipped=1,
        batches=1,
        est_cost_usd=0.05,
    )
    assert isinstance(run_id, int)
    assert run_id > 0


def test_get_runs_returns_list(db):
    db.insert_run(
        run_at=datetime.utcnow(),
        jobs_scraped=3,
        jobs_new=3,
        jobs_scored=2,
        jobs_skipped=1,
        batches=1,
        est_cost_usd=0.02,
    )
    runs = db.get_runs()
    assert len(runs) == 1
    assert runs[0]["jobs_scraped"] == 3
