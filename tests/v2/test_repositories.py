import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.repositories.database import init_db, purge_old_data, utcnow_iso
from app.repositories.decision_repository import DecisionRepository
from app.repositories.memory_repository import MemoryRepository
from app.repositories.observability_repository import ObservabilityRepository
from app.repositories.resume_repository import ResumeRepository
from app.repositories.score_repository import ScoreRepository
from app.repositories.step_repository import StepRepository
from app.repositories.user_repository import UserRepository
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
    "tailored_resumes", "resume_clinic_reviews", "reports",
    "human_decisions", "user_config", "step_executions", "agent_events",
    "llm_calls", "run_metrics", "security_events", "memory_items",
    "api_requests", "idempotency_keys", "favorite_jobs",
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


# ─── MemoryRepository (ADR-062: user-scoped) ─────────────────────────────────

def test_memory_upsert_and_fetch(db_path):
    repo = MemoryRepository(db_path)
    repo.upsert("mem_001", "0", "preferred_role", "role",
                {"value": "Principal Architect"}, confidence=90)
    results = repo.get_by_type("0", "preferred_role")
    assert len(results) == 1
    assert results[0]["memory_key"] == "role"


def test_memory_upsert_updates_existing(db_path):
    repo = MemoryRepository(db_path)
    repo.upsert("mem_002", "0", "rejected_pattern", "pure_ic",
                {"value": "No architecture influence"}, confidence=70)
    repo.upsert("mem_002", "0", "rejected_pattern", "pure_ic",
                {"value": "No architecture influence"}, confidence=85)
    results = repo.get_by_type("0", "rejected_pattern")
    assert len(results) == 1
    assert results[0]["confidence"] == 85


def test_memory_is_isolated_per_user(db_path):
    """A profile never sees another profile's memory — the core ADR-062 invariant."""
    repo = MemoryRepository(db_path)
    repo.upsert("mem_a", "0", "preferred_role", "role",
                {"value": "Architect"}, confidence=90)
    repo.upsert("mem_b", "1", "preferred_role", "role",
                {"value": "New-grad SWE"}, confidence=90)
    user0 = repo.get_by_type("0", "preferred_role")
    user1 = repo.get_by_type("1", "preferred_role")
    assert len(user0) == 1 and user0[0]["memory_value_json"]
    assert len(user1) == 1
    assert "Architect" in user0[0]["memory_value_json"]
    assert "New-grad SWE" in user1[0]["memory_value_json"]
    # get_by_key is likewise scoped
    assert repo.get_by_key("0", "preferred_role", "role") is not None
    assert repo.get_by_key("2", "preferred_role", "role") is None


# ─── UserRepository (ADR-062) ────────────────────────────────────────────────

def test_user_create_returns_id_one_then_two(db_path):
    repo = UserRepository(db_path)
    first = repo.create("Son", note="new-grad SWE")
    second = repo.create("Friend")
    assert first == 1   # 0 is the pre-seeded default
    assert second == 2


def test_user_list_includes_default_first(db_path):
    repo = UserRepository(db_path)
    repo.create("Son")
    users = repo.list_all()
    assert [u["id"] for u in users] == [0, 1]
    assert users[0]["name"] == "Primary"


def test_user_get_and_exists(db_path):
    repo = UserRepository(db_path)
    new_id = repo.create("Son", note="note here")
    got = repo.get_by_id(new_id)
    assert got["name"] == "Son"
    assert got["note"] == "note here"
    assert repo.exists(new_id) is True
    assert repo.exists(0) is True       # default user
    assert repo.exists(999) is False
    # string ids resolve too (the identity seam passes decimal strings)
    assert repo.exists("0") is True


# ─── ResumeRepository (ADR-062: per-user) ────────────────────────────────────

def _make_resume(repo, resume_id, user_id, text_hash="h"):
    repo.create(resume_id=resume_id, user_id=user_id, file_name="r.pdf",
                raw_text="text", parsed_profile={"resume_id": resume_id},
                raw_text_hash=text_hash)


def test_resume_active_is_per_user(db_path):
    """Creating a resume only deactivates the same user's prior resumes."""
    repo = ResumeRepository(db_path)
    _make_resume(repo, "u0_r1", "0")
    _make_resume(repo, "u1_r1", "1")
    _make_resume(repo, "u0_r2", "0")  # deactivates u0_r1 only
    assert repo.get_active("0")["id"] == "u0_r2"
    assert repo.get_active("1")["id"] == "u1_r1"   # user 1 untouched


def test_resume_hash_cache_is_per_user(db_path):
    repo = ResumeRepository(db_path)
    _make_resume(repo, "u0_r1", "0", text_hash="same")
    _make_resume(repo, "u1_r1", "1", text_hash="same")
    assert repo.get_by_raw_text_hash("0", "same")["id"] == "u0_r1"
    assert repo.get_by_raw_text_hash("1", "same")["id"] == "u1_r1"
    assert repo.get_by_raw_text_hash("2", "same") is None


def test_resume_list_by_user(db_path):
    repo = ResumeRepository(db_path)
    _make_resume(repo, "u0_r1", "0")
    _make_resume(repo, "u0_r2", "0")
    _make_resume(repo, "u1_r1", "1")
    assert {r["id"] for r in repo.list_by_user("0")} == {"u0_r1", "u0_r2"}
    assert {r["id"] for r in repo.list_by_user("1")} == {"u1_r1"}


# ─── delete (ADR-062 cooperative scoping) ─────────────────────────────────────

def test_resume_delete_returns_count_and_removes_row(db_path):
    repo = ResumeRepository(db_path)
    _make_resume(repo, "u0_r1", "0")
    _make_resume(repo, "u0_r2", "0")
    assert repo.delete("u0_r1", "0") == 1
    remaining = {r["id"] for r in repo.list_by_user("0")}
    assert remaining == {"u0_r2"}


def test_resume_delete_unknown_id_no_ops(db_path):
    repo = ResumeRepository(db_path)
    _make_resume(repo, "u0_r1", "0")
    assert repo.delete("does-not-exist", "0") == 0
    assert {r["id"] for r in repo.list_by_user("0")} == {"u0_r1"}


def test_resume_delete_cross_user_no_ops(db_path):
    """Cooperative scoping: trying to delete user 0's resume with user 1's id
    is a no-op, not a failure."""
    repo = ResumeRepository(db_path)
    _make_resume(repo, "u0_r1", "0")
    assert repo.delete("u0_r1", "1") == 0
    assert {r["id"] for r in repo.list_by_user("0")} == {"u0_r1"}


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


_OLD_TS = "2020-01-01T00:00:00.000Z"


def _insert_run(conn, run_id, ts, resume_id=None):
    conn.execute(
        "INSERT INTO workflow_runs (id, workflow_type, status, state_json, "
        "resume_id, started_at, updated_at) VALUES (?, 'full', 'completed', '{}', ?, ?, ?)",
        (run_id, resume_id, ts, ts),
    )


def test_purge_cascades_run_children(db_path):
    """ADR-070: purging an expired run deletes ALL its child rows; a recent run and
    its children survive (no cross-run over-deletion)."""
    conn = sqlite3.connect(str(db_path))
    _insert_run(conn, "old_run", _OLD_TS)
    _insert_run(conn, "new_run", utcnow_iso())
    # children of the OLD run (one per representative child table)
    conn.execute("INSERT INTO job_scores (id, workflow_run_id, job_id, resume_id, score_json, created_at) "
                 "VALUES ('s1','old_run','j1','r1','{}',?)", (_OLD_TS,))
    conn.execute("INSERT INTO tailored_resumes (id, workflow_run_id, job_id, resume_id, tailored_json, created_at) "
                 "VALUES ('t1','old_run','j1','r1','{}',?)", (_OLD_TS,))
    conn.execute("INSERT INTO resume_clinic_reviews (id, user_id, resume_id, workflow_run_id, review_json, overhaul_json, created_at) "
                 "VALUES ('c1','0','r1','old_run','{}','{}',?)", (_OLD_TS,))
    conn.execute("INSERT INTO human_decisions (id, workflow_run_id, presented_at, decided_at) "
                 "VALUES ('d1','old_run',?,?)", (_OLD_TS, _OLD_TS))
    # a child of the NEW run that must survive
    conn.execute("INSERT INTO job_scores (id, workflow_run_id, job_id, resume_id, score_json, created_at) "
                 "VALUES ('s2','new_run','j2','r2','{}',?)", (utcnow_iso(),))
    conn.commit()
    conn.close()

    results = purge_old_data(db_path, config={"retention": {"workflow_runs_days": 90}})

    assert results["workflow_runs"] == 1
    assert results["job_scores"] == 1          # only s1 cascaded; s2 (new run) kept
    assert results["tailored_resumes"] == 1
    assert results["resume_clinic_reviews"] == 1
    assert results["human_decisions"] == 1

    conn = sqlite3.connect(str(db_path))
    assert [r[0] for r in conn.execute("SELECT id FROM workflow_runs")] == ["new_run"]
    assert [r[0] for r in conn.execute("SELECT id FROM job_scores")] == ["s2"]
    assert conn.execute("SELECT COUNT(*) FROM tailored_resumes").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM human_decisions").fetchone()[0] == 0
    conn.close()


def test_purge_keeps_active_resume_regardless_of_age(db_path):
    """The user's active resume is never purged, even when old."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO resumes (id, is_active, created_at) VALUES ('active_old', 1, ?)", (_OLD_TS,))
    conn.commit()
    conn.close()

    results = purge_old_data(db_path, config={"retention": {"resumes_days": 365}})
    assert results["resumes"] == 0


def test_purge_deletes_inactive_unreferenced_resume(db_path):
    """An inactive resume past the window, referenced only by a purged run, is deleted."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO resumes (id, is_active, created_at) VALUES ('stale', 0, ?)", (_OLD_TS,))
    _insert_run(conn, "old_run", _OLD_TS, resume_id="stale")  # the only referer is expired
    conn.commit()
    conn.close()

    results = purge_old_data(db_path, config={"retention": {"workflow_runs_days": 90, "resumes_days": 365}})
    assert results["workflow_runs"] == 1
    assert results["resumes"] == 1


def test_purge_keeps_inactive_resume_referenced_by_surviving_run(db_path):
    """The reference guard: an old inactive resume still backing a NON-purged run
    survives (a resume can back multiple runs - cache-keyed by raw_text_hash)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO resumes (id, is_active, created_at) VALUES ('shared', 0, ?)", (_OLD_TS,))
    _insert_run(conn, "old_run", _OLD_TS, resume_id="shared")        # expired
    _insert_run(conn, "recent_run", utcnow_iso(), resume_id="shared")  # surviving referer
    conn.commit()
    conn.close()

    results = purge_old_data(db_path, config={"retention": {"workflow_runs_days": 90, "resumes_days": 365}})
    assert results["workflow_runs"] == 1   # only old_run
    assert results["resumes"] == 0         # 'shared' kept: still referenced by recent_run

    conn = sqlite3.connect(str(db_path))
    assert [r[0] for r in conn.execute("SELECT id FROM resumes")] == ["shared"]
    conn.close()
