import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.repositories.database import init_db, purge_old_data, utcnow_iso
from app.repositories.decision_repository import DecisionRepository
from app.repositories.memory_repository import MemoryRepository
from app.repositories.observability_repository import ObservabilityRepository
from app.repositories.score_repository import ScoreRepository
from app.repositories.step_repository import StepRepository
from app.repositories.workflow_repository import WorkflowRepository


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test_v2.db"
    init_db(path)
    return path


# ─── Schema / init ───────────────────────────────────────────────────────────

_EXPECTED_TABLES = {
    "users", "workflow_runs", "jobs", "resumes", "job_scores",
    "review_rounds", "resume_reviews", "career_advice", "interview_prep",
    "tailored_resumes", "reports", "human_decisions", "user_config",
    "step_executions", "agent_events", "llm_calls", "run_metrics",
    "security_events", "memory_items",
}


def test_all_tables_created(db_path):
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    conn.close()
    created = {r[0] for r in rows}
    assert _EXPECTED_TABLES == created


# ─── ADR-062: multi-user migration ───────────────────────────────────────────

def _columns(db_path, table):
    conn = sqlite3.connect(str(db_path))
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    conn.close()
    return cols


def test_default_user_zero_seeded(db_path):
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT id, name FROM users WHERE id = 0").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 0
    assert row[1] == "Primary"


def test_resumes_and_memory_have_user_id(db_path):
    assert "user_id" in _columns(db_path, "resumes")
    assert "user_id" in _columns(db_path, "memory_items")


def test_new_users_autoincrement_from_one(db_path):
    """User 0 is reserved; the first profile created afterward gets id 1."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO users (name, note, created_at) VALUES (?, ?, ?)",
        ("Son", "new-grad SWE", utcnow_iso()),
    )
    conn.commit()
    new_id = conn.execute("SELECT id FROM users WHERE name = 'Son'").fetchone()[0]
    conn.close()
    assert new_id == 1


def test_backfill_assigns_preexisting_rows_to_user_zero(db_path):
    """Rows inserted with a NULL user_id (legacy data) are ported to '0' on the
    next init_db run. Idempotent: re-running does not change already-ported rows."""
    conn = sqlite3.connect(str(db_path))
    now = utcnow_iso()
    # Simulate legacy rows with no owner.
    conn.execute(
        "INSERT INTO resumes (id, file_name, raw_text, version, is_active, created_at) "
        "VALUES ('legacy_resume', 'old.pdf', 'text', 1, 1, ?)", (now,),
    )
    conn.execute(
        "INSERT INTO memory_items (id, memory_type, memory_value_json, created_at, updated_at) "
        "VALUES ('legacy_mem', 'pref', '{}', ?, ?)", (now, now),
    )
    conn.execute(
        "INSERT INTO workflow_runs (id, workflow_type, status, state_json, started_at, updated_at) "
        "VALUES ('legacy_wf', 'full', 'completed', '{}', ?, ?)", (now, now),
    )
    conn.execute(
        "INSERT INTO user_config (id, config_key, config_value_json, created_at, updated_at) "
        "VALUES ('legacy_cfg', 'search.roles', '[]', ?, ?)", (now, now),
    )
    conn.commit()
    conn.close()

    init_db(db_path)  # re-run triggers the backfill

    conn = sqlite3.connect(str(db_path))
    assert conn.execute("SELECT user_id FROM resumes WHERE id='legacy_resume'").fetchone()[0] == "0"
    assert conn.execute("SELECT user_id FROM memory_items WHERE id='legacy_mem'").fetchone()[0] == "0"
    assert conn.execute("SELECT user_id FROM workflow_runs WHERE id='legacy_wf'").fetchone()[0] == "0"
    assert conn.execute("SELECT user_id FROM user_config WHERE id='legacy_cfg'").fetchone()[0] == "0"
    # Idempotent: still exactly one user-0 row after repeated init.
    assert conn.execute("SELECT COUNT(*) FROM users WHERE id=0").fetchone()[0] == 1
    conn.close()


def test_utcnow_iso_format():
    ts = utcnow_iso()
    assert ts.endswith("Z")
    assert "T" in ts
    assert len(ts) == 24  # YYYY-MM-DDTHH:MM:SS.mmmZ


# ─── WorkflowRepository ──────────────────────────────────────────────────────

def test_workflow_create_and_get(db_path):
    repo = WorkflowRepository(db_path)
    state = {
        "status": "initialized",
        "current_step": "initialized",
        "user_id": "user_1",
        "resume_id": None,
    }
    repo.create("wf_001", "full_career_review", state)
    result = repo.get_by_id("wf_001")
    assert result is not None
    assert result["workflow_type"] == "full_career_review"
    assert result["status"] == "initialized"
    assert result["state"]["user_id"] == "user_1"


def test_workflow_get_returns_none_for_missing(db_path):
    repo = WorkflowRepository(db_path)
    assert repo.get_by_id("nonexistent") is None


def test_workflow_update_state(db_path):
    repo = WorkflowRepository(db_path)
    state = {"status": "initialized", "current_step": "initialized"}
    repo.create("wf_002", "scoring", state)
    updated = {"status": "running", "current_step": "scoring"}
    repo.update_state("wf_002", updated)
    result = repo.get_by_id("wf_002")
    assert result["status"] == "running"


def test_workflow_list_recent(db_path):
    repo = WorkflowRepository(db_path)
    for i in range(3):
        repo.create(f"wf_{i}", "test", {"status": "initialized", "current_step": "initialized"})
    results = repo.list_recent(limit=10)
    assert len(results) == 3


# ─── ScoreRepository ─────────────────────────────────────────────────────────

def test_score_create_and_fetch(db_path):
    repo = ScoreRepository(db_path)
    score = {"overall_score": 85, "technical_score": 90}
    repo.create("sc_001", "wf_001", "job_001", "res_001", score)
    results = repo.get_by_workflow_run("wf_001")
    assert len(results) == 1
    assert results[0]["overall_score"] == 85


def test_score_ordered_by_score_desc(db_path):
    repo = ScoreRepository(db_path)
    repo.create("sc_1", "wf_x", "job_1", "r", {"overall_score": 60})
    repo.create("sc_2", "wf_x", "job_2", "r", {"overall_score": 90})
    repo.create("sc_3", "wf_x", "job_3", "r", {"overall_score": 75})
    results = repo.get_by_workflow_run("wf_x")
    scores = [r["overall_score"] for r in results]
    assert scores == sorted(scores, reverse=True)


# ─── StepRepository ──────────────────────────────────────────────────────────

def test_step_create_and_complete(db_path):
    repo = StepRepository(db_path)
    repo.create("step_001", "wf_001", "scoring")
    steps = repo.get_by_run("wf_001")
    assert len(steps) == 1
    assert steps[0]["status"] == "started"
    assert steps[0]["completed_at"] is None

    repo.complete("step_001", notes="Scored 20 jobs")
    steps = repo.get_by_run("wf_001")
    assert steps[0]["status"] == "completed"
    assert steps[0]["completed_at"] is not None
    assert steps[0]["duration_ms"] is not None
    assert steps[0]["notes"] == "Scored 20 jobs"


def test_step_fail(db_path):
    repo = StepRepository(db_path)
    repo.create("step_002", "wf_002", "job_discovery")
    repo.fail("step_002", notes="Scraper blocked")
    steps = repo.get_by_run("wf_002")
    assert steps[0]["status"] == "failed"
    assert steps[0]["notes"] == "Scraper blocked"


def test_steps_ordered_by_started_at(db_path):
    repo = StepRepository(db_path)
    repo.create("s1", "wf_ord", "initialized")
    repo.create("s2", "wf_ord", "scoring")
    repo.create("s3", "wf_ord", "completed")
    steps = repo.get_by_run("wf_ord")
    assert [s["step"] for s in steps] == ["initialized", "scoring", "completed"]


# ─── DecisionRepository ──────────────────────────────────────────────────────

def test_decision_stores_both_timestamps(db_path):
    repo = DecisionRepository(db_path)
    presented = "2026-04-28T10:00:00.000Z"
    decided = "2026-04-28T10:01:30.000Z"
    repo.create("dec_001", "wf_001", "select_jobs", "approve",
                {"selected": ["job_1"]}, presented, decided)
    results = repo.get_by_run("wf_001")
    assert len(results) == 1
    assert results[0]["presented_at"] == presented
    assert results[0]["decided_at"] == decided


# ─── ObservabilityRepository ─────────────────────────────────────────────────

def test_run_metrics_create_and_update(db_path):
    repo = ObservabilityRepository(db_path)
    started = utcnow_iso()
    repo.create_run_metrics("m_001", "wf_001", started)
    repo.update_run_metrics("wf_001", total_llm_calls=5, total_tokens_input=1000,
                            total_tokens_output=500, total_cost=0.05,
                            total_duration_ms=12000,
                            completed_at=utcnow_iso())
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT * FROM run_metrics WHERE workflow_run_id = ?", ("wf_001",)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[2] == 5   # total_llm_calls


# ─── MemoryRepository ────────────────────────────────────────────────────────

def test_memory_upsert_and_fetch(db_path):
    repo = MemoryRepository(db_path)
    repo.upsert("mem_001", "preferred_role", "role",
                {"value": "Principal Architect"}, confidence=90)
    results = repo.get_by_type("preferred_role")
    assert len(results) == 1
    assert results[0]["memory_key"] == "role"


def test_memory_upsert_updates_existing(db_path):
    repo = MemoryRepository(db_path)
    repo.upsert("mem_002", "rejected_pattern", "pure_ic",
                {"value": "No architecture influence"}, confidence=70)
    repo.upsert("mem_002", "rejected_pattern", "pure_ic",
                {"value": "No architecture influence"}, confidence=85)
    results = repo.get_by_type("rejected_pattern")
    assert len(results) == 1
    assert results[0]["confidence"] == 85


# ─── purge_old_data ──────────────────────────────────────────────────────────

def test_purge_removes_old_rows(db_path):
    conn = sqlite3.connect(str(db_path))
    old_ts = "2020-01-01T00:00:00.000Z"
    conn.execute(
        "INSERT INTO jobs (id, created_at) VALUES ('old_job', ?)", (old_ts,)
    )
    conn.execute(
        "INSERT INTO jobs (id, created_at) VALUES ('new_job', ?)", (utcnow_iso(),)
    )
    conn.commit()
    conn.close()

    results = purge_old_data(db_path, config={"retention": {"jobs_days": 90}})
    assert results["jobs"] == 1

    conn = sqlite3.connect(str(db_path))
    remaining = conn.execute("SELECT id FROM jobs").fetchall()
    conn.close()
    assert [r[0] for r in remaining] == ["new_job"]


def test_purge_does_not_remove_recent_rows(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO jobs (id, created_at) VALUES ('recent_job', ?)", (utcnow_iso(),)
    )
    conn.commit()
    conn.close()

    results = purge_old_data(db_path, config={"retention": {"jobs_days": 90}})
    assert results["jobs"] == 0
