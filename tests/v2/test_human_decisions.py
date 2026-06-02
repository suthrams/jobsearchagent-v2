"""human_decisions audit-trail wiring tests (ADR-074 Gap 1).

The `human_decisions` table was dead (zero writers) until ADR-074: decisions were
persisted only in domain tables (`tailored_resumes` / `resume_clinic_reviews`).
These tests guard the cross-cutting audit trail the same way ADR-073 guards
security_events:

  1. Forcing-function — log_human_decision / log_artifact_decision must have call
     sites (so the table cannot go dead again).
  2. Behavioral — log_artifact_decision writes a human_decisions row with the
     expected type/value and a PII-safe payload.
  3. Scoping — DecisionRepository.list_for_user scopes by profile, COALESCEs
     orphan rows to "0"; decisions_summary aggregates.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.repositories.database import init_db
from app.repositories.decision_repository import DecisionRepository
from app.repositories.observability_repository import ObservabilityRepository
from app.repositories.security_repository import SecurityRepository
from app.repositories.step_repository import StepRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.services import system_health as sh
from app.services.observability_service import (
    ObservabilityService,
    log_artifact_decision,
)

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


def test_human_decision_has_emit_sites():
    """log_human_decision must be reachable and log_artifact_decision must be
    called from the decision endpoints — else the human_decisions audit trail is
    dead again (its pre-ADR-074 state)."""
    helper_calls = 0   # log_artifact_decision(...) call sites
    method_defs = 0    # log_human_decision reachable from the helper
    for py in APP_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for line in text.splitlines():
            s = line.strip()
            if "log_artifact_decision(" in line and not s.startswith("def "):
                helper_calls += 1
            if "log_human_decision(" in line:
                method_defs += 1
    assert helper_calls >= 2, f"expected >=2 log_artifact_decision call sites, found {helper_calls}"
    assert method_defs >= 1, "log_human_decision is never reached"


# ── Layer 2: behavioral ───────────────────────────────────────────────────────


def test_log_artifact_decision_writes_row(db_path):
    log_artifact_decision(
        _obs(db_path),
        workflow_id="run-1",
        decision_type="tailoring",
        decision_value="approve",
        presented_at="2026-06-02T10:00:00.000Z",
        payload={"tailoring_id": "t-1", "job_id": "j-1", "edited": False},
    )
    rows = DecisionRepository(db_path).get_by_run("run-1")
    assert len(rows) == 1
    assert rows[0]["decision_type"] == "tailoring"
    assert rows[0]["decision_value"] == "approve"
    assert rows[0]["presented_at"] == "2026-06-02T10:00:00.000Z"
    assert rows[0]["decided_at"]  # stamped now
    payload = json.loads(rows[0]["payload_json"])
    assert payload == {"tailoring_id": "t-1", "job_id": "j-1", "edited": False}


def test_decision_payload_is_pii_safe(db_path):
    """A well-formed decision payload carries ids + flags only — no free text that
    could echo resume content."""
    log_artifact_decision(
        _obs(db_path),
        workflow_id="run-1",
        decision_type="resume_clinic",
        decision_value="edit",
        presented_at=None,
        payload={"review_id": "c-1", "job_id": None, "edited": True},
    )
    raw = DecisionRepository(db_path).get_by_run("run-1")[0]["payload_json"]
    payload = json.loads(raw)
    assert set(payload) <= {"tailoring_id", "review_id", "job_id", "edited"}


# ── Layer 3: scoping + aggregation ────────────────────────────────────────────


def _seed(db_path):
    wf = WorkflowRepository(db_path)
    dec = DecisionRepository(db_path)
    wf.create("run-u1", "job_search", {"user_id": "1", "status": "completed"})
    wf.create("run-u0", "resume_clinic", {"user_id": "0", "status": "completed"})
    dec.create("d1", "run-u1", "tailoring", "approve", {"tailoring_id": "t1"},
               "2026-06-02T10:00:00.000Z", "2026-06-02T10:01:00.000Z")
    dec.create("d2", "run-u0", "resume_clinic", "reject", {"review_id": "c1"},
               "2026-06-02T10:00:00.000Z", "2026-06-02T10:02:00.000Z")
    # orphan decision (no workflow_runs row) -> COALESCE to "0"
    dec.create("d3", "orphan-run", "tailoring", "edit", {"tailoring_id": "t9"},
               "2026-06-02T10:00:00.000Z", "2026-06-02T10:03:00.000Z")


def test_list_for_user_scopes_by_profile(db_path):
    _seed(db_path)
    dec = DecisionRepository(db_path)
    assert {r["id"] for r in dec.list_for_user("1")} == {"d1"}
    assert {r["id"] for r in dec.list_for_user("0")} == {"d2", "d3"}   # incl orphan
    assert {r["id"] for r in dec.list_for_user()} == {"d1", "d2", "d3"}


def test_decisions_summary_counts(db_path):
    _seed(db_path)
    summ = sh.decisions_summary(user_id=None, db_path=db_path)
    assert summ["total"] == 3
    assert summ["by_type"] == {"tailoring": 2, "resume_clinic": 1}
    assert summ["by_value"] == {"approve": 1, "reject": 1, "edit": 1}
