"""Repository for the tailored_resumes table — Tailoring Agent drafts awaiting approval."""
import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class TailoringRepository:
    """Reads and writes tailored_resumes. approved defaults to 0; approve() sets it to 1
    only after the user has reviewed and accepted the draft via HITL."""
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create(self, tailoring_id: str, workflow_run_id: str, job_id: str,
               resume_id: str, tailored: dict) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO tailored_resumes
                   (id, workflow_run_id, job_id, resume_id, tailored_json, approved, created_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (tailoring_id, workflow_run_id, job_id, resume_id, json.dumps(tailored), now),
            )

    def approve(self, tailoring_id: str) -> None:
        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE tailored_resumes SET approved = 1 WHERE id = ?",
                (tailoring_id,),
            )

    def get_by_run_job(self, workflow_run_id: str, job_id: str) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                """SELECT * FROM tailored_resumes
                   WHERE workflow_run_id = ? AND job_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (workflow_run_id, job_id),
            ).fetchone()
        return dict(row) if row else None
