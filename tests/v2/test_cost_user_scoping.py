"""Per-profile scoping of the cost dashboard aggregates (ADR-062).

llm_calls carries no user_id; ownership is the user_id of the workflow_runs row
its workflow_run_id points at. These tests pin that a user_id filter returns only
that profile's spend, that None returns everything, and that orphan calls (no
workflow_runs row) fall to the default profile "0".
"""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from app.repositories.database import init_db
from app.services.cost_breakdown import (
    all_runs_by_cost,
    compute_dashboard_aggregate,
    top_calls_by_cost,
    top_runs_by_cost,
)


def _seed_run(db_path: Path, wf_id: str, user_id: str | None) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """INSERT INTO workflow_runs
               (id, workflow_type, status, current_step, state_json, user_id,
                started_at, updated_at)
               VALUES (?, 'full_career_review', 'completed', 'completed',
                       '{}', ?, '2026-05-26T00:00:00Z', '2026-05-26T00:01:00Z')""",
            (wf_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_call(db_path: Path, wf_id: str, cost: float) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """INSERT INTO llm_calls
               (id, workflow_run_id, agent_name, provider, model,
                tokens_input, tokens_output, estimated_cost, latency_ms, created_at)
               VALUES (?, ?, 'scoring_agent', 'claude', 'claude-haiku-4-5-20251001',
                       100, 50, ?, 100, '2026-05-26T00:00:30Z')""",
            (str(uuid.uuid4()), wf_id, cost),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def two_profile_db(tmp_path):
    db = tmp_path / "v2.db"
    init_db(db)
    _seed_run(db, "wf-owner0", "0")
    _seed_run(db, "wf-owner1", "1")
    _seed_call(db, "wf-owner0", 0.10)
    _seed_call(db, "wf-owner1", 0.99)
    return db


def test_dashboard_aggregate_scopes_to_profile(two_profile_db):
    db = two_profile_db
    own0 = compute_dashboard_aggregate(db_path=db, user_id="0")
    assert own0["totals"]["calls"] == 1
    assert own0["totals"]["cost_usd"] == pytest.approx(0.10)

    own1 = compute_dashboard_aggregate(db_path=db, user_id="1")
    assert own1["totals"]["calls"] == 1
    assert own1["totals"]["cost_usd"] == pytest.approx(0.99)


def test_dashboard_aggregate_none_is_system_wide(two_profile_db):
    allp = compute_dashboard_aggregate(db_path=two_profile_db, user_id=None)
    assert allp["totals"]["calls"] == 2
    assert allp["totals"]["cost_usd"] == pytest.approx(1.09)


def test_orphan_calls_count_toward_default_profile(tmp_path):
    """A call whose run has no workflow_runs row belongs to profile 0."""
    db = tmp_path / "v2.db"
    init_db(db)
    _seed_call(db, "wf-orphan", 0.42)  # no workflow_runs row seeded

    assert compute_dashboard_aggregate(db_path=db, user_id="0")["totals"]["calls"] == 1
    assert compute_dashboard_aggregate(db_path=db, user_id="1")["totals"]["calls"] == 0
    assert compute_dashboard_aggregate(db_path=db, user_id=None)["totals"]["calls"] == 1


def test_top_and_all_runs_respect_profile(two_profile_db):
    db = two_profile_db
    runs0 = {r["workflow_run_id"] for r in top_runs_by_cost(n=10, db_path=db, user_id="0")}
    assert runs0 == {"wf-owner0"}
    all1 = {r["workflow_run_id"] for r in all_runs_by_cost(db_path=db, user_id="1")}
    assert all1 == {"wf-owner1"}


def test_top_calls_respect_profile(two_profile_db):
    calls1 = top_calls_by_cost(n=10, db_path=two_profile_db, user_id="1")
    assert all(c["workflow_run_id"] == "wf-owner1" for c in calls1)
    assert len(calls1) == 1
