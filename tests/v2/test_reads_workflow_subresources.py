"""Run-scoped read-services + /workflows/* read endpoints (ADR-075 Phases 4-6)."""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.repositories.database import init_db, utcnow_iso
from app.services.reads import workflow_reads as wr


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "v2.db"
    init_db(path)
    return path


def _seed(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO workflow_runs (id, workflow_type, status, state_json, "
                 "user_id, started_at, updated_at) VALUES ('wf','full','completed',?,'0',?,?)",
                 (json.dumps({"effective_config": {"x": 1}}), utcnow_iso(), utcnow_iso()))
    conn.execute("INSERT INTO jobs (id, title, company, created_at) VALUES ('j','T','Acme',?)",
                 (utcnow_iso(),))
    conn.execute("INSERT INTO job_scores (id, workflow_run_id, job_id, resume_id, score_json, "
                 "overall_score, created_at) VALUES ('s','wf','j','r',?,88,?)",
                 (json.dumps({"technical_score": 88}), utcnow_iso()))
    conn.execute("INSERT INTO agent_events (id, workflow_run_id, agent_name, event_type, status, "
                 "created_at) VALUES ('e','wf','scoring_agent','completed','completed',?)",
                 (utcnow_iso(),))
    conn.execute("INSERT INTO llm_calls (id, workflow_run_id, agent_name, model, tokens_input, "
                 "tokens_output, estimated_cost, latency_ms, created_at) "
                 "VALUES ('l','wf','scoring_agent','m',10,5,0.01,100,?)", (utcnow_iso(),))
    conn.commit()
    conn.close()


def test_read_services_shapes(db_path):
    _seed(db_path)
    assert wr.list_workflow_jobs("wf", db_path=db_path)["total"] == 1
    assert wr.list_agent_events("wf", db_path=db_path)["total"] == 1
    assert wr.list_llm_calls("wf", db_path=db_path)["total"] == 1
    assert wr.list_step_executions("wf", db_path=db_path)["total"] == 0
    pipe = wr.get_job_pipeline("wf", "j", db_path=db_path)
    assert pipe["job"]["title"] == "T"
    assert pipe["score"]["data"]["overall_score"] == 88
    detail = wr.get_workflow_run_detail("wf", db_path=db_path)
    assert detail["state"]["effective_config"]["x"] == 1


def test_endpoints_shape_via_stub(monkeypatch):
    import app.api.routers.reads as reads
    monkeypatch.setattr(reads.wr, "list_workflow_jobs",
                        lambda *a, **k: {"items": [{"job_id": "j"}], "total": 1, "limit": 1, "offset": 0})
    monkeypatch.setattr(reads.wr, "get_job_pipeline", lambda *a, **k: {"job": {"id": "j"}})
    monkeypatch.setattr(reads.wr, "get_workflow_run_detail", lambda *a, **k: None)
    client = TestClient(app)
    r1 = client.get("/workflows/wf/scored-jobs")
    assert r1.status_code == 200 and set(r1.json()) == {"items", "total", "limit", "offset"}
    r2 = client.get("/workflows/wf/jobs/j/pipeline")
    assert r2.status_code == 200 and r2.json()["job"]["id"] == "j"
    # detail 404 when absent
    assert client.get("/workflows/missing/detail").status_code == 404


def test_recent_route_does_not_collide_with_workflow_id(monkeypatch):
    """GET /workflows/recent must hit the recent handler, not /{workflow_id}."""
    import app.api.routers.reads as reads
    monkeypatch.setattr(reads.wr, "list_recent_workflows",
                        lambda *a, **k: {"items": [{"workflow_id": "abc"}], "total": 1, "limit": 1, "offset": 0})
    client = TestClient(app)
    resp = client.get("/workflows/recent")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["workflow_id"] == "abc"
