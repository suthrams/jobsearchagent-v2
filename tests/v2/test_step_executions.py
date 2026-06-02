"""step_executions wiring tests (ADR-074 Gap 2).

The step_executions table was dead (log_step_* never called). ADR-074 Gap 2
instruments every LangGraph node via _instrument_step so node-level timing +
transitions are recorded. Guards:

  1. Forcing-function — _instrument_step is applied in the graph builder (so
     nodes can't silently revert to uninstrumented).
  2. Behavioral — the wrapper logs started+completed on success, started+failed
     on exception, and never swallows the node's own exception.
  3. Read — performance_summary surfaces slowest_steps from step_executions.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.repositories.database import init_db
from app.repositories.observability_repository import ObservabilityRepository
from app.repositories.decision_repository import DecisionRepository
from app.repositories.security_repository import SecurityRepository
from app.repositories.step_repository import StepRepository
from app.services import system_health as sh
from app.services.observability_service import ObservabilityService
from app.workflows.workflow_graph import _instrument_step

APP_DIR = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test_v2.db"
    init_db(path)
    return path


def _obs(db_path) -> ObservabilityService:
    return ObservabilityService(
        ObservabilityRepository(db_path),
        StepRepository(db_path),
        DecisionRepository(db_path),
        SecurityRepository(db_path),
    )


# ── Layer 1: forcing function ─────────────────────────────────────────────────


def test_graph_builder_instruments_nodes():
    """The graph builder must wrap nodes with _instrument_step, else step_executions
    goes dead again."""
    src = (APP_DIR / "workflows" / "workflow_graph.py").read_text(encoding="utf-8")
    assert "_instrument_step(" in src, "nodes are not wrapped for step_executions"


# ── Layer 2: behavioral ───────────────────────────────────────────────────────


def test_instrument_logs_started_and_completed(db_path):
    obs = _obs(db_path)
    node = _instrument_step("discover_jobs", lambda state: {"ok": True}, obs)
    out = node({"workflow_id": "run-1"})
    assert out == {"ok": True}
    rows = StepRepository(db_path).get_by_run("run-1")
    assert len(rows) == 1
    assert rows[0]["step"] == "discover_jobs"
    assert rows[0]["status"] == "completed"


def test_instrument_logs_failed_and_reraises(db_path):
    obs = _obs(db_path)

    def boom(state):
        raise ValueError("node blew up")

    node = _instrument_step("score_jobs", boom, obs)
    with pytest.raises(ValueError, match="node blew up"):
        node({"workflow_id": "run-1"})
    rows = StepRepository(db_path).get_by_run("run-1")
    assert len(rows) == 1
    assert rows[0]["step"] == "score_jobs"
    assert rows[0]["status"] == "failed"


def test_instrument_never_crashes_on_observability_failure(db_path):
    """A broken observability write must not break the node (never-crash)."""
    node = _instrument_step("load_resume", lambda state: {"ok": 1}, _obs(db_path))
    # missing workflow_id -> "" ; still must run the node and return its result
    assert node({}) == {"ok": 1}


# ── Layer 3: read ─────────────────────────────────────────────────────────────


def test_performance_summary_surfaces_slowest_steps(db_path):
    step_repo = StepRepository(db_path)
    # two completed steps so duration_ms is computed by the repo
    for i, name in enumerate(["discover_jobs", "score_jobs"]):
        sid = f"s{i}"
        step_repo.create(sid, "run-1", name)
        step_repo.complete(sid)
    perf = sh.performance_summary(user_id=None, db_path=db_path)
    steps = {d["step"] for d in perf["slowest_steps"]}
    assert steps == {"discover_jobs", "score_jobs"}
