"""Per-run rollup tests (ADR-074 Gap 3).

In-graph runs get a run_metrics row (init at register_run, finalize at
generate_report); out-of-graph runs (clinic/tailoring/deep-review/interview-prep)
write a workflow_runs row but no run_metrics. system_health.run_metrics_rollup is
the lazy read the ADR prefers: it returns the finalized row if present, else
derives totals from llm_calls + wall-clock span from timestamps - so per-run
metrics are available for EVERY run without init/finalize plumbing in each runner.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.repositories.database import init_db, utcnow_iso
from app.repositories.observability_repository import ObservabilityRepository
from app.services import system_health as sh


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test_v2.db"
    init_db(path)
    return path


def _llm_call(db_path, run_id, ti, to, cost, created_at):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO llm_calls
           (id, workflow_run_id, agent_name, provider, model, tokens_input,
            tokens_output, estimated_cost, latency_ms, created_at)
           VALUES (?, ?, 'a', 'claude', 'm', ?, ?, ?, 100, ?)""",
        (f"{run_id}-{created_at}", run_id, ti, to, cost, created_at),
    )
    conn.commit()
    conn.close()


def test_rollup_computed_from_llm_calls_when_no_run_metrics_row(db_path):
    """The out-of-graph case: no run_metrics row -> derive from llm_calls."""
    _llm_call(db_path, "ooG", 100, 20, 0.01, "2026-06-02T10:00:00.000Z")
    _llm_call(db_path, "ooG", 200, 30, 0.02, "2026-06-02T10:00:05.000Z")
    r = sh.run_metrics_rollup("ooG", db_path=db_path)
    assert r["computed"] is True
    assert r["calls"] == 2
    assert r["tokens_input"] == 300
    assert r["tokens_output"] == 50
    assert abs(r["cost_usd"] - 0.03) < 1e-9
    assert r["duration_ms"] == 5000  # 5s span


def test_rollup_prefers_finalized_run_metrics_row(db_path):
    """The in-graph case: a finalized run_metrics row is returned as-is."""
    repo = ObservabilityRepository(db_path)
    repo.create_run_metrics("m1", "inG", started_at="2026-06-02T09:00:00.000Z")
    repo.update_run_metrics(
        "inG", total_llm_calls=7, total_tokens_input=1000,
        total_tokens_output=200, total_cost=0.5, total_duration_ms=12345,
        completed_at=utcnow_iso(),
    )
    # an llm_calls row that would yield different numbers if (wrongly) recomputed
    _llm_call(db_path, "inG", 1, 1, 0.99, "2026-06-02T09:00:01.000Z")
    r = sh.run_metrics_rollup("inG", db_path=db_path)
    assert r["computed"] is False
    assert r["calls"] == 7
    assert r["cost_usd"] == 0.5
    assert r["duration_ms"] == 12345


def test_rollup_empty_run(db_path):
    r = sh.run_metrics_rollup("nope", db_path=db_path)
    assert r["calls"] == 0 and r["duration_ms"] == 0
