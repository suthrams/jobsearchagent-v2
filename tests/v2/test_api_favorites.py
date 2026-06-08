"""Tests for the /users/{id}/favorites router - "My favorite jobs" (ADR-090)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_favorite_repo, get_job_repo
from app.api.main import app
from app.repositories.database import get_connection, init_db, utcnow_iso
from app.repositories.favorite_repository import MAX_FAVORITES, FavoriteRepository
from app.repositories.job_repository import JobRepository


def _seed_job(db_path, job_id: str, title: str, company: str) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO jobs (id, title, company, created_at) VALUES (?, ?, ?, ?)",
            (job_id, title, company, utcnow_iso()),
        )


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "v2.db"
    init_db(db_path)
    _seed_job(db_path, "job-a", "Staff Engineer", "Acme")
    app.dependency_overrides[get_favorite_repo] = lambda: FavoriteRepository(db_path)
    app.dependency_overrides[get_job_repo] = lambda: JobRepository(db_path)
    with TestClient(app) as c:
        c._db_path = db_path  # for tests that need to seed more jobs
        yield c
    app.dependency_overrides.clear()


def test_add_favorite_snapshots_title_and_company(client):
    resp = client.post("/users/1/favorites", json={"workflow_id": "wf-1", "job_id": "job-a"})
    assert resp.status_code == 201
    fav = resp.json()["favorite"]
    assert fav["job_id"] == "job-a"
    assert fav["title"] == "Staff Engineer"   # snapshotted server-side from the job row
    assert fav["company"] == "Acme"


def test_add_unknown_job_is_404(client):
    resp = client.post("/users/1/favorites", json={"workflow_id": "wf-1", "job_id": "ghost"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "job_not_found"


def test_list_is_scoped_to_the_profile(client):
    client.post("/users/1/favorites", json={"workflow_id": "wf", "job_id": "job-a"})
    assert [f["job_id"] for f in client.get("/users/1/favorites").json()["favorites"]] == ["job-a"]
    assert client.get("/users/2/favorites").json()["favorites"] == []


def test_add_is_idempotent(client):
    client.post("/users/1/favorites", json={"workflow_id": "wf", "job_id": "job-a"})
    client.post("/users/1/favorites", json={"workflow_id": "wf", "job_id": "job-a"})
    assert len(client.get("/users/1/favorites").json()["favorites"]) == 1


def test_remove_is_idempotent_and_204(client):
    client.post("/users/1/favorites", json={"workflow_id": "wf", "job_id": "job-a"})
    assert client.delete("/users/1/favorites/job-a").status_code == 204
    assert client.delete("/users/1/favorites/job-a").status_code == 204  # again, no error
    assert client.get("/users/1/favorites").json()["favorites"] == []


def test_cap_returns_409(client):
    for i in range(MAX_FAVORITES):
        _seed_job(client._db_path, f"job-{i}", f"Role {i}", "Acme")
        r = client.post("/users/1/favorites", json={"workflow_id": "wf", "job_id": f"job-{i}"})
        assert r.status_code == 201
    _seed_job(client._db_path, "job-over", "One too many", "Acme")
    over = client.post("/users/1/favorites", json={"workflow_id": "wf", "job_id": "job-over"})
    assert over.status_code == 409
    assert over.json()["detail"] == "favorites_cap_reached"
