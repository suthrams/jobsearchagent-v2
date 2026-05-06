"""Workflow History cost column reads from llm_calls (truth source).

Previously the cost_usd column on the History table came from
state_json.run_metrics.estimated_cost_usd (the in-memory aggregator).
The aggregator is lossy and was empty in production for weeks, so the
History page reported zero or stale cost.

Fix: db_reader.load_persisted_workflow_runs now COALESCEs the SUM of
llm_calls.estimated_cost first, falling back to the state_json value
only for runs that predate the observability fix.

This file pins that behavior. If a future refactor reverts to reading
state_json directly, these tests fail.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from app.repositories.database import init_db
from app.ui import db_reader


def _seed_workflow(db_path: Path, wf_id: str, state_json: dict | None = None) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """INSERT INTO workflow_runs
               (id, workflow_type, status, current_step, state_json,
                started_at, updated_at)
               VALUES (?, 'full_career_review', 'completed', 'completed',
                       ?, '2026-05-01T00:00:00Z', '2026-05-01T00:01:00Z')""",
            (wf_id, json.dumps(state_json or {})),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_llm_call(db_path: Path, wf_id: str, cost: float, ti: int = 100, to: int = 50) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """INSERT INTO llm_calls
               (id, workflow_run_id, agent_name, provider, model,
                tokens_input, tokens_output, estimated_cost, latency_ms,
                created_at)
               VALUES (?, ?, 'scoring_agent', 'claude',
                       'claude-haiku-4-5-20251001', ?, ?, ?, 100,
                       '2026-05-01T00:00:30Z')""",
            (str(uuid.uuid4()), wf_id, ti, to, cost),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def db_path(tmp_path, monkeypatch) -> Path:
    """Init a tmp DB and point db_reader.DB_PATH at it for the duration of the test."""
    path = tmp_path / "history.db"
    init_db(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    # Streamlit caches results; clear so each test starts clean.
    db_reader.load_persisted_workflow_runs.clear()
    return path


def test_history_cost_uses_llm_calls_when_present(db_path):
    """The truth source is llm_calls.SUM(estimated_cost). state_json is the
    fallback only — when llm_calls has rows, they win."""
    # state_json says $0.10 but llm_calls says $0.025 — llm_calls must win.
    _seed_workflow(db_path, "wf-truth-1", {
        "run_metrics": {"estimated_cost_usd": 0.10, "llm_calls": 99}
    })
    _seed_llm_call(db_path, "wf-truth-1", cost=0.010)
    _seed_llm_call(db_path, "wf-truth-1", cost=0.015)

    df = db_reader.load_persisted_workflow_runs()
    row = df[df["workflow_id"] == "wf-truth-1"].iloc[0]
    assert row["cost_usd"] == pytest.approx(0.025)
    assert int(row["llm_calls"]) == 2


def test_history_cost_falls_back_to_state_json_when_no_llm_calls(db_path):
    """Older runs that completed before the observability fix have no
    llm_calls rows. The state_json estimate is still useful as fallback."""
    db_reader.load_persisted_workflow_runs.clear()
    _seed_workflow(db_path, "wf-legacy-1", {
        "run_metrics": {"estimated_cost_usd": 0.42, "llm_calls": 38}
    })
    # No llm_call rows seeded — simulating a pre-fix run.

    df = db_reader.load_persisted_workflow_runs()
    row = df[df["workflow_id"] == "wf-legacy-1"].iloc[0]
    assert row["cost_usd"] == pytest.approx(0.42)
    assert int(row["llm_calls"]) == 38


def test_history_cost_zero_when_neither_source_has_data(db_path):
    """Runs with no llm_calls AND no state_json metrics show $0, not NULL."""
    db_reader.load_persisted_workflow_runs.clear()
    _seed_workflow(db_path, "wf-empty-1", {})  # no run_metrics in state_json

    df = db_reader.load_persisted_workflow_runs()
    row = df[df["workflow_id"] == "wf-empty-1"].iloc[0]
    assert row["cost_usd"] == 0.0
    assert int(row["llm_calls"]) == 0
