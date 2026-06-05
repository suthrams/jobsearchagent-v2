"""Tests for run-lifecycle controls: idempotent kickoff + in-flight guard
(ADR-082) and cooperative cancellation (ADR-083).

Reuses the mocked-graph TestClient harness from test_api_workflows.py.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.api.dependencies import get_graph
from app.api.main import app
from app.repositories.database import init_db
from app.repositories.idempotency_repository import IdempotencyRepository
from app.workflows import run_control
from app.workflows.workflow_graph import _instrument_step, build_graph

from tests.v2.test_api_workflows import _initial_state, _make_deps


# ── run_control unit tests ──────────────────────────────────────────────────────

def test_in_flight_guard_single_flight():
    wfid = "wf-guard-1"
    run_control.release_running(wfid)
    assert run_control.try_acquire_running(wfid) is True
    assert run_control.is_running(wfid) is True
    # A second acquire while held is refused.
    assert run_control.try_acquire_running(wfid) is False
    run_control.release_running(wfid)
    assert run_control.is_running(wfid) is False
    # Releasable again after release.
    assert run_control.try_acquire_running(wfid) is True
    run_control.release_running(wfid)


def test_cancel_registry_roundtrip():
    wfid = "wf-cancelreg-1"
    run_control.clear_cancel(wfid)
    assert run_control.is_cancel_requested(wfid) is False
    run_control.request_cancel(wfid)
    assert run_control.is_cancel_requested(wfid) is True
    run_control.request_cancel(wfid)  # idempotent
    assert run_control.is_cancel_requested(wfid) is True
    run_control.clear_cancel(wfid)
    assert run_control.is_cancel_requested(wfid) is False


# ── _instrument_step cancellation (ADR-083) ─────────────────────────────────────

def test_instrument_step_raises_when_cancel_requested():
    wfid = "wf-instr-cancel"
    ran = []
    obs = MagicMock()
    wrapped = _instrument_step("score_jobs", lambda s: ran.append(1) or {"ok": 1}, obs)
    run_control.request_cancel(wfid)
    try:
        with pytest.raises(run_control.WorkflowCancelled):
            wrapped({"workflow_id": wfid})
    finally:
        run_control.clear_cancel(wfid)
    assert ran == [], "node body must not run when cancel is requested"
    obs.log_step_started.assert_not_called()


def test_instrument_step_runs_normally_without_cancel():
    wfid = "wf-instr-ok"
    run_control.clear_cancel(wfid)
    ran = []
    obs = MagicMock()
    obs.log_step_started.return_value = "step-1"
    wrapped = _instrument_step("score_jobs", lambda s: ran.append(1) or {"ok": 1}, obs)
    result = wrapped({"workflow_id": wfid})
    assert result == {"ok": 1}
    assert ran == [1]
    obs.log_step_started.assert_called_once()


# ── Idempotent kickoff (ADR-082) ────────────────────────────────────────────────

@pytest.fixture
def idem_repo(tmp_path, monkeypatch):
    db = tmp_path / "idem.db"
    init_db(db)
    repo = IdempotencyRepository(db)
    monkeypatch.setattr(
        "app.api.routers.workflows._get_idempotency_repo", lambda: repo,
    )
    return repo


@pytest.fixture
def client():
    saver = MemorySaver()
    deps = _make_deps(checkpointer=saver)
    graph = build_graph(deps)
    app.dependency_overrides[get_graph] = lambda: graph
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


_BODY = {"resume_id": "res-001", "search_criteria": {"roles": ["Staff Engineer"]}}


def test_idempotent_kickoff_replays_same_key_same_body(client, idem_repo):
    key = "idem-key-aaa"
    r1 = client.post("/workflows", headers={"Idempotency-Key": key}, json=_BODY)
    r2 = client.post("/workflows", headers={"Idempotency-Key": key}, json=_BODY)
    assert r1.status_code == 202 and r2.status_code == 202
    # Replay returns the SAME workflow_id (no second run minted).
    assert r1.json()["workflow_id"] == r2.json()["workflow_id"]


def test_idempotent_kickoff_conflict_same_key_different_body(client, idem_repo):
    key = "idem-key-bbb"
    r1 = client.post("/workflows", headers={"Idempotency-Key": key}, json=_BODY)
    assert r1.status_code == 202
    other = {"resume_id": "res-999", "search_criteria": {"roles": ["Principal"]}}
    r2 = client.post("/workflows", headers={"Idempotency-Key": key}, json=other)
    assert r2.status_code == 409
    assert r2.json()["detail"]["error"] == "idempotency_key_reused"


def test_kickoff_without_key_starts_distinct_runs(client, idem_repo):
    r1 = client.post("/workflows", json=_BODY)
    r2 = client.post("/workflows", json=_BODY)
    assert r1.status_code == 202 and r2.status_code == 202
    # No key -> each call is its own run (backward compatible).
    assert r1.json()["workflow_id"] != r2.json()["workflow_id"]


# ── Cancel endpoint (ADR-083) ───────────────────────────────────────────────────

def _mock_graph_with_state(values: dict, next_steps: tuple):
    g = MagicMock()
    g.get_state.return_value = SimpleNamespace(values=values, next=next_steps)
    return g


def _client_with_graph(graph):
    app.dependency_overrides[get_graph] = lambda: graph
    return TestClient(app)


def test_cancel_not_found():
    graph = _mock_graph_with_state({}, ())
    graph.get_state.return_value = SimpleNamespace(values={}, next=())
    with _client_with_graph(graph) as c:
        r = c.post("/workflows/missing/cancel")
    app.dependency_overrides.clear()
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "workflow_not_found"


def test_cancel_not_cancellable_when_no_pending_steps():
    graph = _mock_graph_with_state({"status": "completed"}, ())
    with _client_with_graph(graph) as c:
        r = c.post("/workflows/wf-done/cancel")
    app.dependency_overrides.clear()
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "workflow_not_cancellable"


def test_cancel_running_returns_202_and_registers():
    wfid = "wf-cancel-202"
    run_control.clear_cancel(wfid)
    graph = _mock_graph_with_state({"status": "running"}, ("score_jobs",))
    with _client_with_graph(graph) as c:
        r = c.post(f"/workflows/{wfid}/cancel")
    app.dependency_overrides.clear()
    try:
        assert r.status_code == 202
        assert r.json()["status"] == "cancelling"
        assert run_control.is_cancel_requested(wfid) is True
        graph.update_state.assert_called_once()
    finally:
        run_control.clear_cancel(wfid)


def test_retry_rejected_when_already_running():
    wfid = "wf-retry-busy"
    graph = _mock_graph_with_state({"status": "running"}, ("score_jobs",))
    assert run_control.try_acquire_running(wfid) is True  # simulate in-flight
    try:
        with _client_with_graph(graph) as c:
            r = c.post(f"/workflows/{wfid}/retry")
        app.dependency_overrides.clear()
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "workflow_already_running"
    finally:
        run_control.release_running(wfid)


def test_read_status_terminal_wins_over_cancel_flag(client):
    """A completed run reports 'completed' even if a stale cancel flag is set
    (ADR-083 status precedence: terminal status wins)."""
    tid = "wf-status-terminal"
    graph = app.dependency_overrides[get_graph]()
    graph.invoke(_initial_state(tid), {"configurable": {"thread_id": tid}})
    run_control.request_cancel(tid)  # stale flag should not override a terminal run
    try:
        resp = client.get(f"/workflows/{tid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"
    finally:
        run_control.clear_cancel(tid)
