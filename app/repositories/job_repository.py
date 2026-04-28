"""Repository for the jobs table — normalised job postings from all scrapers."""
import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class JobRepository:
    """Reads and writes jobs. Upserts on id so re-fetching the same job is idempotent."""
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def upsert(self, job: dict) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO jobs
                   (id, source, source_job_id, title, company, location,
                    job_description, normalized_job_json, url, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       normalized_job_json = excluded.normalized_job_json""",
                (
                    job["id"],
                    job.get("source"),
                    job.get("source_job_id"),
                    job.get("title"),
                    job.get("company"),
                    job.get("location"),
                    job.get("job_description"),
                    json.dumps(job.get("normalized", {})),
                    job.get("url"),
                    now,
                ),
            )

    def get_by_id(self, job_id: str) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_by_company(self, company: str) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE company = ? ORDER BY created_at DESC",
                (company,),
            ).fetchall()
        return [dict(r) for r in rows]
