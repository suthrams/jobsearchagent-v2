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

    def url_exists(self, url: str) -> bool:
        """Whether ANY row in `jobs` carries this URL. Retained for
        backward compatibility; the discovery dedup path no longer uses it
        (it dropped multi-profile + multi-run re-discovery). New callers
        should prefer `url_excluded` or `url_scored_by_user`."""
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM jobs WHERE url = ? LIMIT 1", (url,)
            ).fetchone()
        return row is not None

    def url_excluded(self, url: str) -> bool:
        """Whether this URL is currently flagged excluded.

        Used by the discovery dedup path so excluded URLs are never
        re-surfaced (ADR-057 "stay excluded"). Does NOT drop URLs that are
        merely persisted from a prior run - those can be re-discovered by
        a different profile, which is a feature, not a bug.
        """
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM jobs WHERE url = ? AND excluded = 1 LIMIT 1", (url,)
            ).fetchone()
        return row is not None

    def url_scored_by_user(self, url: str, user_id: str) -> bool:
        """Whether THIS user has already scored a job at this URL in a
        prior workflow run.

        Used by the discovery dedup path as the per-user cost saver: if a
        user has already paid to score this URL, don't pay again on
        re-runs. Different users (profiles) score independently - a job
        scored by Primary may legitimately be re-scored by Vishal under
        different criteria.
        """
        if not user_id:
            return False
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                """SELECT 1 FROM job_scores js
                   JOIN workflow_runs w ON js.workflow_run_id = w.id
                   JOIN jobs j ON js.job_id = j.id
                   WHERE j.url = ? AND w.user_id = ?
                   LIMIT 1""",
                (url, user_id),
            ).fetchone()
        return row is not None

    def get_by_company(self, company: str) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE company = ? ORDER BY created_at DESC",
                (company,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Exclusion (ADR-057) ──────────────────────────────────────────────────
    # `excluded` is a pipeline-filter flag, NOT application-tracking metadata.
    # See ADR-057 for the filter-vs-tracker distinction. upsert() above does
    # NOT touch the exclusion columns, so re-discoveries of the same job_id
    # preserve the prior flag.

    def set_excluded(self, job_id: str, reason: str | None = None) -> None:
        """Mark a job excluded. Filtered out of discovery + analytics views."""
        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE jobs SET excluded = 1, excluded_reason = ?, excluded_at = ? WHERE id = ?",
                (reason, utcnow_iso(), job_id),
            )

    def clear_excluded(self, job_id: str) -> None:
        """Un-exclude a previously excluded job. Rare; provided for completeness."""
        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE jobs SET excluded = 0, excluded_reason = NULL, excluded_at = NULL WHERE id = ?",
                (job_id,),
            )

    def excluded_set(self) -> set[str]:
        """All currently-excluded job_ids. Cheap; called once per workflow run."""
        with get_connection(self.db_path) as conn:
            rows = conn.execute("SELECT id FROM jobs WHERE excluded = 1").fetchall()
        return {r["id"] for r in rows}

    def list_excluded(self) -> list[dict]:
        """All excluded jobs, newest exclusion first. Powers GET /jobs/excluded."""
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE excluded = 1 ORDER BY excluded_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
