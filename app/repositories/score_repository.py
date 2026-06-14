"""Repository for the job_scores table — Scoring Agent output per job per run."""
import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class ScoreRepository:
    """Reads and writes job_scores. overall_score is stored as a top-level column
    (not only inside score_json) to allow efficient ORDER BY and threshold filtering."""
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create(self, score_id: str, workflow_run_id: str, job_id: str,
               resume_id: str, score: dict,
               research_context: dict | None = None) -> None:
        """Persist a score. ADR-105: the Research Agent's per-job output that INFORMED
        this score is stored alongside it (1:1) so it is no longer discarded. The param
        defaults to None for back-compat with old call sites + old rows."""
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            # OR IGNORE + the UNIQUE(workflow_run_id, job_id) index (fix 2) make a
            # concurrent double-submit a no-op instead of a duplicate score row.
            conn.execute(
                """INSERT OR IGNORE INTO job_scores
                   (id, workflow_run_id, job_id, resume_id, score_json, overall_score,
                    research_context_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    score_id,
                    workflow_run_id,
                    job_id,
                    resume_id,
                    json.dumps(score),
                    score.get("overall_score"),
                    json.dumps(research_context) if research_context is not None else None,
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
