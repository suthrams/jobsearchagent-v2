"""Workflow read-service + GET /workflows endpoint tests (ADR-075 Phase 1)."""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app.api.routers.workflows as workflows_router
from app.api.main import app
from app.repositories.database import init_db, utcnow_iso
from app.services.reads.workflow_reads import list_workflow_runs


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "v2.db"
    init_db(path)
    return path


def _run(db_path, wf_id, user_id, started_at, *, state=None):
    state = state or {}
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO workflow_runs (id, workflow_type, status, current_step, "
        "state_json, user_id, started_at, updated_at) "
        "VALUES (?, 'full', 'completed', 'completed', ?, ?, ?, ?)",
        (wf_id, json.dumps(state), user_id, started_at, started_at),
    )
    conn.commit()
    conn.close()


def _score(db_path, wf_id, score, created_at=None):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO job_scores (id, workflow_run_id, job_id, resume_id, "
        "score_json, overall_score, created_at) VALUES (?, ?, 'j', 'r', '{}', ?, ?)",
        (f"{wf_id}-{score}", wf_id, score, created_at or utcnow_iso()),
    )
    conn.commit()
    conn.close()


# ── read-service ──────────────────────────────────────────────────────────────


def test_list_returns_envelope_and_scopes_by_profile(db_path):
    _run(db_path, "a", "1", "2026-06-01T10:00:00.000Z")
    _run(db_path, "b", "1", "2026-06-02T10:00:00.000Z")
    _run(db_path, "c", "0", "2026-06-02T11:00:00.000Z")
    page = list_workflow_runs(user_id="1", limit=50, db_path=db_path)
    assert set(page) == {"items", "total", "limit", "offset"}
    assert page["total"] == 2
    assert {r["workflow_id"] for r in page["items"]} == {"a", "b"}
    # default sort started_at desc
    assert [r["workflow_id"] for r in page["items"]] == ["b", "a"]


def test_paging_limit_and_offset(db_path):
    for i in range(5):
        _run(db_path, f"r{i}", "0", f"2026-06-0{i + 1}T10:00:00.000Z")
    p1 = list_workflow_runs(user_id="0", limit=2, offset=0, db_path=db_path)
    p2 = list_workflow_runs(user_id="0", limit=2, offset=2, db_path=db_path)
    assert p1["total"] == 5 and len(p1["items"]) == 2
    assert len(p2["items"]) == 2
    assert {r["workflow_id"] for r in p1["items"]} != {r["workflow_id"] for r in p2["items"]}


def test_sort_allowlist_falls_back_on_unknown_field(db_path):
    _run(db_path, "a", "0", "2026-06-01T10:00:00.000Z")
    # an injection-y sort must not raise and must fall back to the default order
    page = list_workflow_runs(user_id="0", limit=50, sort="id; DROP TABLE", db_path=db_path)
    assert page["total"] == 1


def test_legacy_fallback_when_no_workflow_runs(db_path):
    # job_scores exist but the run has no workflow_runs row -> legacy derived rows
    _score(db_path, "ghost", 80)
    _score(db_path, "ghost", 90)
    page = list_workflow_runs(user_id="0", limit=50, db_path=db_path)
    assert page["total"] == 1
    row = page["items"][0]
    assert row["workflow_id"] == "ghost"
    assert row["jobs_scored"] == 2
    assert row["best_score"] == 90


def test_empty_when_nothing(db_path):
    page = list_workflow_runs(user_id="0", limit=50, db_path=db_path)
    assert page == {"items": [], "total": 0, "limit": 50, "offset": 0}


# ── endpoint contract (stub the service; validate router + response_model) ─────


def test_endpoint_shape_and_param_clamp(monkeypatch):
    captured = {}

    def _stub(*, user_id, limit, offset, sort, order):
        captured.update(user_id=user_id, limit=limit, offset=offset, sort=sort, order=order)
        return {"items": [{"workflow_id": "x", "status": "completed"}],
                "total": 1, "limit": limit, "offset": offset}

    monkeypatch.setattr(workflows_router, "list_workflow_runs", _stub)
    client = TestClient(app)  # no lifespan -> no graph build
    resp = client.get("/workflows?limit=9999&offset=3&sort=cost_usd&user_id=2")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["items"][0]["workflow_id"] == "x"
    # router clamped limit to the 200 ceiling before calling the service
    assert captured["limit"] == 200
    assert captured["offset"] == 3
    assert captured["user_id"] == "2"
