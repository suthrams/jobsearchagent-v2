"""Repository for career_advice and interview_prep tables — post-review agent outputs."""
import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class AdviceRepository:
    """Reads and writes career_advice and interview_prep. Both are keyed by
    workflow_run_id + job_id, matching the per-job deep review structure."""
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create_advice(self, advice_id: str, workflow_run_id: str,
                      job_id: str, advice: dict) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO career_advice
                   (id, workflow_run_id, job_id, advice_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (advice_id, workflow_run_id, job_id, json.dumps(advice), now),
            )

    def create_prep(self, prep_id: str, workflow_run_id: str,
                    job_id: str, prep: dict) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO interview_prep
                   (id, workflow_run_id, job_id, prep_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (prep_id, workflow_run_id, job_id, json.dumps(prep), now),
            )

    def get_advice_by_run_job(self, workflow_run_id: str, job_id: str) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                """SELECT * FROM career_advice
                   WHERE workflow_run_id = ? AND job_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (workflow_run_id, job_id),
            ).fetchone()
        return dict(row) if row else None

    def get_prep_by_run_job(self, workflow_run_id: str, job_id: str) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                """SELECT * FROM interview_prep
                   WHERE workflow_run_id = ? AND job_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (workflow_run_id, job_id),
            ).fetchone()
        return dict(row) if row else None
