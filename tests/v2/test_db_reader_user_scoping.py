"""Per-profile read scoping in db_reader (ADR-062).

History and cross-run analytics show only the active profile's data. Ownership of
every per-run row falls out of workflow_runs.user_id via the workflow_run_id
foreign key; pre-existing / orphan rows COALESCE to the default profile "0".

db_reader functions are @st.cache_data-decorated and read a module-global DB_PATH,
so each test monkeypatches DB_PATH and clears the function's cache before calling.
"""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

import app.ui.db_reader as dbr
from app.repositories.database import init_db


def _run(db: Path, wf_id: str, user_id: str | None) -> None:
    conn = sqlite3.connect(str(db))
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


def _job_score(db: Path, wf_id: str, job_id: str, score: int = 80) -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO jobs (id, source, title, company, location, url, created_at) "
            "VALUES (?, 'adzuna', 'Staff Engineer', 'Acme', 'Remote', 'http://x', "
            "'2026-05-26T00:00:00Z')",
            (job_id,),
        )
        conn.execute(
            """INSERT INTO job_scores
               (id, workflow_run_id, job_id, resume_id, score_json, overall_score, created_at)
               VALUES (?, ?, ?, 'r1', '{"technical_score": 80}', ?, '2026-05-26T00:00:30Z')""",
            (str(uuid.uuid4()), wf_id, job_id, score),
        )
        conn.commit()
    finally:
        conn.close()


def _resume(db: Path, rid: str, user_id: str, active: int = 1) -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO resumes (id, user_id, file_name, is_active, version, created_at) "
            "VALUES (?, ?, ?, ?, 1, '2026-05-26T00:00:00Z')",
            (rid, user_id, f"{rid}.pdf", active),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "v2.db"
    init_db(path)
    monkeypatch.setattr(dbr, "DB_PATH", path)
    # Caches are keyed on args only, not DB_PATH, so clear between tests.
    for fn in (dbr.load_persisted_workflow_runs, dbr.load_workflow_runs,
               dbr.load_scored_jobs, dbr.load_user_resumes):
        fn.clear()
    return path


def test_persisted_runs_scoped_to_profile(db):
    _run(db, "wf-0", "0")
    _run(db, "wf-1", "1")
    own0 = dbr.load_persisted_workflow_runs(user_id="0")
    assert set(own0["workflow_id"]) == {"wf-0"}
    own1 = dbr.load_persisted_workflow_runs(user_id="1")
    assert set(own1["workflow_id"]) == {"wf-1"}
    allp = dbr.load_persisted_workflow_runs(user_id=None)
    assert set(allp["workflow_id"]) == {"wf-0", "wf-1"}


def test_scored_jobs_scoped_to_profile(db):
    _run(db, "wf-0", "0")
    _run(db, "wf-1", "1")
    _job_score(db, "wf-0", "job-a")
    _job_score(db, "wf-1", "job-b")
    own1 = dbr.load_scored_jobs(user_id="1")
    assert set(own1["job_id"]) == {"job-b"}
    allp = dbr.load_scored_jobs(user_id=None)
    assert set(allp["job_id"]) == {"job-a", "job-b"}


def test_scored_jobs_orphan_falls_to_default(db):
    """A score whose run has no workflow_runs row belongs to profile 0."""
    _job_score(db, "wf-orphan", "job-x")  # no workflow_runs row
    assert set(dbr.load_scored_jobs(user_id="0")["job_id"]) == {"job-x"}
    assert dbr.load_scored_jobs(user_id="1").empty


def test_user_resumes_scoped(db):
    _resume(db, "r0", "0")
    _resume(db, "r1", "1")
    own0 = dbr.load_user_resumes("0")
    assert list(own0["resume_id"]) == ["r0"]
    own1 = dbr.load_user_resumes("1")
    assert list(own1["resume_id"]) == ["r1"]


def test_legacy_workflow_runs_scoped(db):
    """The job_scores-derived fallback also scopes by owner."""
    _run(db, "wf-0", "0")
    _run(db, "wf-1", "1")
    _job_score(db, "wf-0", "job-a")
    _job_score(db, "wf-1", "job-b")
    own0 = dbr.load_workflow_runs(user_id="0")
    assert set(own0["id"]) == {"wf-0"}
