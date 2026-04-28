import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class ReviewRepository:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create_round(self, round_id: str, workflow_run_id: str, job_id: str,
                     round_number: int, critic_output: dict,
                     audit_output: dict, stop_reason: str | None = None) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO review_rounds
                   (id, workflow_run_id, job_id, round_number,
                    critic_output_json, audit_output_json,
                    audit_score, auditor_confidence, stop_reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    round_id,
                    workflow_run_id,
                    job_id,
                    round_number,
                    json.dumps(critic_output),
                    json.dumps(audit_output),
                    audit_output.get("audit_score"),
                    audit_output.get("auditor_confidence"),
                    stop_reason,
                    now,
                ),
            )

    def create_review(self, review_id: str, workflow_run_id: str, job_id: str,
                      resume_id: str, review: dict) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO resume_reviews
                   (id, workflow_run_id, job_id, resume_id, review_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (review_id, workflow_run_id, job_id, resume_id, json.dumps(review), now),
            )

    def get_rounds_by_run(self, workflow_run_id: str) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM review_rounds WHERE workflow_run_id = ? ORDER BY round_number ASC",
                (workflow_run_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_review_by_run_job(self, workflow_run_id: str, job_id: str) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                """SELECT * FROM resume_reviews
                   WHERE workflow_run_id = ? AND job_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (workflow_run_id, job_id),
            ).fetchone()
        return dict(row) if row else None
