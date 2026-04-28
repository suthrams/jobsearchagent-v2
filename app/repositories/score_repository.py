import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class ScoreRepository:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create(self, score_id: str, workflow_run_id: str, job_id: str,
               resume_id: str, score: dict) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO job_scores
                   (id, workflow_run_id, job_id, resume_id, score_json, overall_score, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    score_id,
                    workflow_run_id,
                    job_id,
                    resume_id,
                    json.dumps(score),
                    score.get("overall_score"),
                    now,
                ),
            )

    def get_by_workflow_run(self, workflow_run_id: str) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM job_scores WHERE workflow_run_id = ? ORDER BY overall_score DESC",
                (workflow_run_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_by_job(self, job_id: str) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM job_scores WHERE job_id = ? ORDER BY created_at DESC",
                (job_id,),
            ).fetchall()
        return [dict(r) for r in rows]
