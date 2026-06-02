"""User read-service + GET /users/{id}/resumes + clinic-panel scoping (ADR-075 Phase 2)."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

import app.api.routers.users as users_router
from app.api.main import app
from app.repositories.database import init_db, utcnow_iso
from app.repositories.resume_clinic_repository import ResumeClinicRepository
from app.services.reads.user_reads import list_user_resumes


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "v2.db"
    init_db(path)
    return path


def _resume(db_path, rid, user_id, *, active=0, created_at=None):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO resumes (id, user_id, file_name, raw_text, parsed_profile_json, "
        "is_active, version, created_at) VALUES (?, ?, ?, '', '{}', ?, 1, ?)",
        (rid, user_id, f"{rid}.pdf", active, created_at or utcnow_iso()),
    )
    conn.commit()
    conn.close()


# ── read-service ──────────────────────────────────────────────────────────────


def test_list_user_resumes_scopes_and_orders_active_first(db_path):
    _resume(db_path, "a", "1", active=0, created_at="2026-06-02T10:00:00.000Z")
    _resume(db_path, "b", "1", active=1, created_at="2026-06-01T10:00:00.000Z")
    _resume(db_path, "c", "0", active=1)
    page = list_user_resumes("1", db_path=db_path)
    assert set(page) == {"items", "total", "limit", "offset"}
    assert page["total"] == 2
    # active first, then newest
    assert [r["resume_id"] for r in page["items"]] == ["b", "a"]


def test_list_user_resumes_empty(db_path):
    assert list_user_resumes("9", db_path=db_path) == {
        "items": [], "total": 0, "limit": 0, "offset": 0}


# ── endpoint contract (stub the service) ──────────────────────────────────────


def test_resumes_endpoint_shape(monkeypatch):
    monkeypatch.setattr(
        users_router, "list_user_resumes",
        lambda uid: {"items": [{"resume_id": "x", "file_name": "x.pdf",
                                "is_active": 1, "version": 1, "created_at": None}],
                     "total": 1, "limit": 1, "offset": 0},
    )
    client = TestClient(app)
    resp = client.get("/users/1/resumes")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["items"][0]["resume_id"] == "x"


# ── clinic panel scoping: list_by_user excludes tailoring-chat sessions ────────


def test_list_by_user_excludes_job_anchored_sessions(db_path):
    """ADR-072 / ADR-075 Phase 2: the clinic past-runs list (list_by_user) must
    exclude tailoring-chat sessions (job_id NOT NULL)."""
    repo = ResumeClinicRepository(db_path)
    repo.create(clinic_id="plain", user_id="0", resume_id="r1",
                workflow_run_id=None, target_role=None, target_track=None,
                seniority_aware=False, review={}, alignment=None, overhaul={},
                fidelity_review=None)
    repo.create(clinic_id="chat", user_id="0", resume_id="r1",
                workflow_run_id=None, target_role=None, target_track=None,
                seniority_aware=False, review={}, alignment=None, overhaul={},
                fidelity_review=None, source_workflow_run_id="run-1", job_id="job-1")
    ids = {r["id"] for r in repo.list_by_user("0")}
    assert ids == {"plain"}            # the job-anchored "chat" row is excluded
