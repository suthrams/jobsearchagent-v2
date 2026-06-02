"""Dashboard read-service + /dashboard endpoints (ADR-075 Phases 3 + 7)."""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app.api.routers.dashboard as dashboard_router
from app.api.main import app
from app.repositories.database import init_db, utcnow_iso
from app.services.reads.dashboard_reads import list_scored_jobs


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "v2.db"
    init_db(path)
    return path


def _scored(db_path, job_id, user_id, score, *, excluded=0):
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO workflow_runs (id, workflow_type, status, state_json, "
                 "user_id, started_at, updated_at) VALUES (?, 'full', 'completed', '{}', ?, ?, ?)",
                 (f"run-{job_id}", user_id, utcnow_iso(), utcnow_iso()))
    conn.execute("INSERT INTO jobs (id, title, company, excluded, created_at) "
                 "VALUES (?, ?, 'Acme', ?, ?)", (job_id, f"Job {job_id}", excluded, utcnow_iso()))
    conn.execute("INSERT INTO job_scores (id, workflow_run_id, job_id, resume_id, "
                 "score_json, overall_score, created_at) VALUES (?, ?, ?, 'r', ?, ?, ?)",
                 (f"s-{job_id}", f"run-{job_id}", job_id,
                  json.dumps({"technical_score": score}), score, utcnow_iso()))
    conn.commit()
    conn.close()


def test_list_scored_jobs_scopes_and_hides_excluded(db_path):
    _scored(db_path, "j1", "1", 90)
    _scored(db_path, "j2", "1", 80, excluded=1)
    _scored(db_path, "j3", "0", 70)
    page = list_scored_jobs(user_id="1", db_path=db_path)
    assert set(page) == {"items", "total", "limit", "offset"}
    assert {r["job_id"] for r in page["items"]} == {"j1"}          # excluded hidden, profile scoped
    page_inc = list_scored_jobs(user_id="1", include_excluded=True, db_path=db_path)
    assert {r["job_id"] for r in page_inc["items"]} == {"j1", "j2"}
    # carries the per-track score + ordered by overall desc
    assert page["items"][0]["technical_score"] == 90


def test_endpoint_shape(monkeypatch):
    monkeypatch.setattr(dashboard_router, "list_scored_jobs",
                        lambda **k: {"items": [{"job_id": "x"}], "total": 1, "limit": 1, "offset": 0})
    client = TestClient(app)
    resp = client.get("/dashboard/scored-jobs?user_id=0")
    assert resp.status_code == 200
    assert set(resp.json()) == {"items", "total", "limit", "offset"}
    assert resp.json()["items"][0]["job_id"] == "x"
