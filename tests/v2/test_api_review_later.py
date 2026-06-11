"""Tests for the /users/{id}/review-later router - "Maybe / Review later" (ADR-100).

Mirrors the favorites router tests: snapshot, idempotency, per-profile scoping,
cap -> 409, 404 on unknown job. Adds the ADR-100 specifics: the snapshot carries the
link + source (so the list renders the job without the run), and the list is
isolated from favorites (the two kinds share a store but are separate buckets).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_favorite_repo, get_job_repo
from app.api.main import app
from app.repositories.database import get_connection, init_db, utcnow_iso
from app.repositories.favorite_repository import MAX_REVIEW_LATER, FavoriteRepository
from app.repositories.job_repository import JobRepository


def _seed_job(db_path, job_id: str, title: str, company: str,
              url: str | None = None, source: str | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO jobs (id, title, company, url, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, title, company, url, source, utcnow_iso()),
        )


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "v2.db"
    init_db(db_path)
    _seed_job(db_path, "job-a", "SOC Analyst", "Acme",
              url="https://adzuna/job-a", source="adzuna")
    app.dependency_overrides[get_favorite_repo] = lambda: FavoriteRepository(db_path)
    app.dependency_overrides[get_job_repo] = lambda: JobRepository(db_path)
    with TestClient(app) as c:
        c._db_path = db_path
        yield c
    app.dependency_overrides.clear()


def test_add_snapshots_title_company_url_source(client):
    resp = client.post("/users/1/review-later",
                       json={"workflow_id": "wf-1", "job_id": "job-a"})
    assert resp.status_code == 201
    saved = resp.json()["review_later"]
    assert saved["job_id"] == "job-a"
    assert saved["title"] == "SOC Analyst" and saved["company"] == "Acme"
    # The link + source are snapshotted so the list renders without the run.
    assert saved["url"] == "https://adzuna/job-a" and saved["source"] == "adzuna"
    assert saved["kind"] == "review_later"


def test_add_unknown_job_is_404(client):
    resp = client.post("/users/1/review-later",
                       json={"workflow_id": "wf-1", "job_id": "ghost"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "job_not_found"


def test_list_is_scoped_to_the_profile(client):
    client.post("/users/1/review-later", json={"workflow_id": "wf", "job_id": "job-a"})
    got = client.get("/users/1/review-later").json()["review_later"]
    assert [j["job_id"] for j in got] == ["job-a"]
    assert client.get("/users/2/review-later").json()["review_later"] == []


def test_review_later_is_isolated_from_favorites(client):
    # Same job to favorites then review-later: kinds are distinct lists. Because a
    # job is saved once per profile (UNIQUE), moving it to review-later moves the
    # bucket, so it leaves the favorites list.
    client.post("/users/1/favorites", json={"workflow_id": "wf", "job_id": "job-a"})
    client.post("/users/1/review-later", json={"workflow_id": "wf", "job_id": "job-a"})
    assert [j["job_id"] for j in client.get("/users/1/review-later").json()["review_later"]] == ["job-a"]
    assert client.get("/users/1/favorites").json()["favorites"] == []


def test_remove_is_idempotent_and_204(client):
    client.post("/users/1/review-later", json={"workflow_id": "wf", "job_id": "job-a"})
    assert client.delete("/users/1/review-later/job-a").status_code == 204
    assert client.delete("/users/1/review-later/job-a").status_code == 204
    assert client.get("/users/1/review-later").json()["review_later"] == []


def test_cap_returns_409(client):
    for i in range(MAX_REVIEW_LATER):
        _seed_job(client._db_path, f"job-{i}", f"Role {i}", "Acme")
        r = client.post("/users/1/review-later",
                        json={"workflow_id": "wf", "job_id": f"job-{i}"})
        assert r.status_code == 201
    _seed_job(client._db_path, "job-over", "One too many", "Acme")
    over = client.post("/users/1/review-later",
                       json={"workflow_id": "wf", "job_id": "job-over"})
    assert over.status_code == 409
    assert over.json()["detail"] == "review_later_cap_reached"
