"""Per-job exclusion (ADR-057) — repository, router, db_reader, and the
implicit discovery filter via deduplicate.

Exclusion is a pipeline filter, NOT application tracking. These tests cover
that distinction: writes only set the filter flag and recall metadata; reads
apply the filter at the right layer; re-discoveries of excluded URLs are
dropped at deduplicate time without any extra logic in JobDiscoveryService.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_deps, get_graph
from app.api.main import app
from app.repositories.database import init_db, utcnow_iso
from app.repositories.job_repository import JobRepository
from app.services.job_discovery_service import JobDiscoveryService
from app.workflows.workflow_graph import WorkflowDependencies


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path) -> Path:
    path = tmp_path / "test_v2.db"
    init_db(path)
    return path


@pytest.fixture
def repo(db_path) -> JobRepository:
    return JobRepository(db_path=db_path)


def _seed_job(repo: JobRepository, job_id: str = "j1", url: str = "https://example.com/1") -> None:
    repo.upsert({
        "id": job_id, "source": "adzuna", "source_job_id": "1",
        "title": "Staff Engineer", "company": "Acme", "location": "Remote",
        "job_description": "Build distributed systems.",
        "normalized": {"foo": "bar"},
        "url": url,
    })


# ── Repository ───────────────────────────────────────────────────────────────

def test_default_excluded_flag_is_zero(repo, db_path):
    _seed_job(repo)
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT excluded, excluded_reason, excluded_at FROM jobs WHERE id=?", ("j1",)).fetchone()
    conn.close()
    assert row[0] == 0
    assert row[1] is None
    assert row[2] is None


def test_set_excluded_persists_flag_reason_and_timestamp(repo, db_path):
    _seed_job(repo)
    repo.set_excluded("j1", reason="Pay band too low for the role")
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT excluded, excluded_reason, excluded_at FROM jobs WHERE id=?", ("j1",)).fetchone()
    conn.close()
    assert row[0] == 1
    assert row[1] == "Pay band too low for the role"
    assert row[2] is not None and row[2].endswith("Z")  # ISO 8601 UTC


def test_set_excluded_no_reason_persists_null_reason(repo, db_path):
    _seed_job(repo)
    repo.set_excluded("j1")  # no reason
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT excluded, excluded_reason FROM jobs WHERE id=?", ("j1",)).fetchone()
    conn.close()
    assert row[0] == 1
    assert row[1] is None


def test_clear_excluded_resets_all_three_columns(repo, db_path):
    _seed_job(repo)
    repo.set_excluded("j1", reason="trial")
    repo.clear_excluded("j1")
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT excluded, excluded_reason, excluded_at FROM jobs WHERE id=?", ("j1",)).fetchone()
    conn.close()
    assert row == (0, None, None)


def test_excluded_set_returns_only_excluded_ids(repo):
    _seed_job(repo, "j1", "https://example.com/1")
    _seed_job(repo, "j2", "https://example.com/2")
    _seed_job(repo, "j3", "https://example.com/3")
    repo.set_excluded("j1")
    repo.set_excluded("j3")
    assert repo.excluded_set() == {"j1", "j3"}


def test_list_excluded_returns_newest_first(repo):
    _seed_job(repo, "j1", "https://example.com/1")
    _seed_job(repo, "j2", "https://example.com/2")
    repo.set_excluded("j1", reason="first")
    time.sleep(0.01)  # ensure distinct excluded_at timestamps
    repo.set_excluded("j2", reason="second")
    rows = repo.list_excluded()
    assert [r["id"] for r in rows] == ["j2", "j1"]
    assert rows[0]["excluded_reason"] == "second"


def test_upsert_does_not_clobber_excluded_flag(repo, db_path):
    """Re-scrape of the same job_id must NOT reset the user's exclusion."""
    _seed_job(repo, "j1", "https://example.com/1")
    repo.set_excluded("j1", reason="not pursuing")
    # Re-upsert (simulates re-discovery hitting the existing id)
    _seed_job(repo, "j1", "https://example.com/1")
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT excluded, excluded_reason FROM jobs WHERE id=?", ("j1",)).fetchone()
    conn.close()
    assert row == (1, "not pursuing")


# ── Discovery filter (implicit via deduplicate) ──────────────────────────────

def test_deduplicate_drops_url_already_persisted_even_when_excluded(db_path):
    """ADR-057 cost-saving claim: a re-discovered URL whose row is excluded
    is dropped at deduplicate time. The same `url_exists` check that powers
    normal dedup also covers excluded rows for free.
    """
    repo = JobRepository(db_path=db_path)
    _seed_job(repo, "j1", "https://example.com/dup")
    repo.set_excluded("j1")  # user excluded this job

    svc = JobDiscoveryService(job_repository=repo, config={"search": {"max_jobs": 50}})
    # Build a JobPosting whose URL matches the excluded row
    rediscovered = SimpleNamespace(
        url="https://example.com/dup",
        source="adzuna",
        title="Staff Engineer",
        company="Acme",
        location="Remote",
        description="...",
        salary=None,
        found_at=None,
        posted_at=None,
        work_mode=None,
    )
    posting = svc.normalize(rediscovered, workflow_id="wf-1")
    survivors = svc.deduplicate([posting])
    assert survivors == []  # filtered before scoring


# ── Router ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client_with_repo(db_path):
    """TestClient with a real JobRepository (file-backed) wired into get_deps."""
    real_repo = JobRepository(db_path=db_path)
    fake_deps = SimpleNamespace(job_repo=real_repo)

    app.dependency_overrides[get_deps] = lambda: fake_deps
    app.dependency_overrides[get_graph] = lambda: MagicMock()
    try:
        with TestClient(app) as client:
            yield client, real_repo
    finally:
        app.dependency_overrides.clear()


def test_post_exclude_flips_flag(client_with_repo):
    client, repo = client_with_repo
    _seed_job(repo, "j1")

    r = client.post("/jobs/j1/exclude", json={"reason": "too low"})
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == "j1"
    assert body["excluded"] is True
    assert "j1" in repo.excluded_set()


def test_post_exclude_with_no_body_works(client_with_repo):
    client, repo = client_with_repo
    _seed_job(repo, "j1")

    r = client.post("/jobs/j1/exclude")  # body omitted
    assert r.status_code == 200
    assert r.json()["excluded"] is True


def test_post_exclude_unknown_job_404(client_with_repo):
    client, _ = client_with_repo

    r = client.post("/jobs/does-not-exist/exclude", json={"reason": None})
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "job_not_found"


def test_delete_exclude_clears_flag(client_with_repo):
    client, repo = client_with_repo
    _seed_job(repo, "j1")
    repo.set_excluded("j1", reason="trial")

    r = client.delete("/jobs/j1/exclude")
    assert r.status_code == 200
    assert r.json()["excluded"] is False
    assert repo.excluded_set() == set()


def test_delete_exclude_unknown_job_404(client_with_repo):
    client, _ = client_with_repo
    r = client.delete("/jobs/does-not-exist/exclude")
    assert r.status_code == 404


def test_get_excluded_lists_with_metadata(client_with_repo):
    client, repo = client_with_repo
    _seed_job(repo, "j1")
    _seed_job(repo, "j2")
    repo.set_excluded("j1", reason="pay")
    repo.set_excluded("j2", reason="commute")

    r = client.get("/jobs/excluded")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    job_ids = {j["job_id"] for j in body["jobs"]}
    assert job_ids == {"j1", "j2"}
    reasons = {j["job_id"]: j["excluded_reason"] for j in body["jobs"]}
    assert reasons == {"j1": "pay", "j2": "commute"}
