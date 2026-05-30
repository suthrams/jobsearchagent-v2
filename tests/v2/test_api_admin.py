"""Tests for the /admin router — the ADR-070 data-retention purge endpoint."""
from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

import app.api.routers.admin as admin_module
from app.api.main import app
from app.repositories.database import init_db

_OLD_TS = "2020-01-01T00:00:00.000Z"


class _StubConfig:
    """Avoids the real config.yaml dependency; supplies just the retention block."""

    def __init__(self, *a, **k):
        pass

    def get_effective_config(self, *a, **k):
        return {"retention": {"workflow_runs_days": 90}}


def test_admin_purge_runs_and_returns_rows_deleted_map(tmp_path, monkeypatch):
    db = tmp_path / "v2.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO workflow_runs (id, workflow_type, status, state_json, started_at, updated_at) "
        "VALUES ('old','full','completed','{}',?,?)",
        (_OLD_TS, _OLD_TS),
    )
    conn.execute(
        "INSERT INTO job_scores (id, workflow_run_id, job_id, resume_id, score_json, created_at) "
        "VALUES ('s1','old','j','r','{}',?)",
        (_OLD_TS,),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(admin_module, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(admin_module, "ConfigService", _StubConfig)

    client = TestClient(app)
    resp = client.post("/admin/purge")

    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow_runs"] == 1   # the expired run
    assert body["job_scores"] == 1      # its cascaded child
