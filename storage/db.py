# storage/db.py
# ─────────────────────────────────────────────────────────────────────────────
# SQLite database layer for persisting Job objects between runs.
# Uses the standard library sqlite3 module — no ORM dependency.
# Jobs are stored as a mix of typed columns and a JSON blob for flexible fields.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from models.job import Job, JobSource, ApplicationStatus, TrackScores, SalaryRange, WorkMode

logger = logging.getLogger(__name__)

# SQL to create the jobs table if it does not exist
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    url              TEXT    NOT NULL UNIQUE,   -- deduplicated on URL
    source           TEXT    NOT NULL,
    title            TEXT    NOT NULL,
    company          TEXT    NOT NULL,
    location         TEXT,
    state            TEXT,                      -- US state abbreviation (e.g. 'GA', 'TX')
    work_mode        TEXT,
    description      TEXT,
    salary_json      TEXT,                      -- SalaryRange serialised as JSON
    scores_json      TEXT,                      -- TrackScores serialised as JSON
    status           TEXT    NOT NULL DEFAULT 'new',
    posted_at        TEXT,                      -- ISO 8601 string
    expires_at       TEXT,
    found_at         TEXT    NOT NULL,
    applied_at       TEXT,
    score_ic         INTEGER,                   -- IC engineer track score (0-100)
    score_architect  INTEGER,                   -- Architect track score (0-100)
    score_management INTEGER,                   -- Management/Director track score (0-100)
    score_best       INTEGER,                   -- Max across all active tracks
    excluded         INTEGER NOT NULL DEFAULT 0, -- 1 = hidden from all views
    excluded_reason  TEXT                        -- Why the job was excluded
);
"""

# Columns added after initial schema — applied via ALTER TABLE on existing databases
_MIGRATIONS = [
    ("score_ic",         "ALTER TABLE jobs ADD COLUMN score_ic         INTEGER"),
    ("score_architect",  "ALTER TABLE jobs ADD COLUMN score_architect  INTEGER"),
    ("score_management", "ALTER TABLE jobs ADD COLUMN score_management INTEGER"),
    ("score_best",       "ALTER TABLE jobs ADD COLUMN score_best       INTEGER"),
    ("excluded",         "ALTER TABLE jobs ADD COLUMN excluded         INTEGER NOT NULL DEFAULT 0"),
    ("excluded_reason",  "ALTER TABLE jobs ADD COLUMN excluded_reason  TEXT"),
    ("state",            "ALTER TABLE jobs ADD COLUMN state            TEXT"),
]

# Run history table — one row per main.py execution
CREATE_RUNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at        TEXT    NOT NULL,   -- ISO 8601 UTC timestamp
    jobs_scraped  INTEGER NOT NULL DEFAULT 0,  -- total returned by all scrapers
    jobs_new      INTEGER NOT NULL DEFAULT 0,  -- newly inserted (not duplicates)
    jobs_scored   INTEGER NOT NULL DEFAULT 0,  -- successfully scored by Claude
    jobs_skipped  INTEGER NOT NULL DEFAULT 0,  -- stale / no description / excluded
    batches       INTEGER NOT NULL DEFAULT 0,  -- number of Claude API batches
    est_cost_usd  REAL    NOT NULL DEFAULT 0.0, -- estimated USD cost (Sonnet pricing)
    -- Actual token usage per operation (populated from API response metadata)
    tokens_input_scoring    INTEGER NOT NULL DEFAULT 0,
    tokens_output_scoring   INTEGER NOT NULL DEFAULT 0,
    tokens_input_parsing    INTEGER NOT NULL DEFAULT 0,
    tokens_output_parsing   INTEGER NOT NULL DEFAULT 0,
    tokens_input_tailoring  INTEGER NOT NULL DEFAULT 0,
    tokens_output_tailoring INTEGER NOT NULL DEFAULT 0,
    actual_cost_usd         REAL    NOT NULL DEFAULT 0.0, -- cost from real token counts
    -- Phase latency (wall-clock seconds measured with time.perf_counter)
    elapsed_scrape_s        REAL    NOT NULL DEFAULT 0.0, -- scraping phase duration
    elapsed_score_s         REAL    NOT NULL DEFAULT 0.0, -- scoring phase duration (filter + Claude calls)
    elapsed_total_s         REAL    NOT NULL DEFAULT 0.0, -- end-to-end run duration
    avg_batch_latency_s     REAL    NOT NULL DEFAULT 0.0, -- mean seconds per Claude batch call
    jobs_per_second         REAL    NOT NULL DEFAULT 0.0  -- scoring throughput
);
"""

# Migrations for the runs table — for databases created before token tracking was added
_RUNS_MIGRATIONS = [
    ("tokens_input_scoring",    "ALTER TABLE runs ADD COLUMN tokens_input_scoring    INTEGER NOT NULL DEFAULT 0"),
    ("tokens_output_scoring",   "ALTER TABLE runs ADD COLUMN tokens_output_scoring   INTEGER NOT NULL DEFAULT 0"),
    ("tokens_input_parsing",    "ALTER TABLE runs ADD COLUMN tokens_input_parsing    INTEGER NOT NULL DEFAULT 0"),
    ("tokens_output_parsing",   "ALTER TABLE runs ADD COLUMN tokens_output_parsing   INTEGER NOT NULL DEFAULT 0"),
    ("tokens_input_tailoring",  "ALTER TABLE runs ADD COLUMN tokens_input_tailoring  INTEGER NOT NULL DEFAULT 0"),
    ("tokens_output_tailoring", "ALTER TABLE runs ADD COLUMN tokens_output_tailoring INTEGER NOT NULL DEFAULT 0"),
    ("actual_cost_usd",         "ALTER TABLE runs ADD COLUMN actual_cost_usd         REAL    NOT NULL DEFAULT 0.0"),
    ("elapsed_scrape_s",        "ALTER TABLE runs ADD COLUMN elapsed_scrape_s        REAL    NOT NULL DEFAULT 0.0"),
    ("elapsed_score_s",         "ALTER TABLE runs ADD COLUMN elapsed_score_s         REAL    NOT NULL DEFAULT 0.0"),
    ("elapsed_total_s",         "ALTER TABLE runs ADD COLUMN elapsed_total_s         REAL    NOT NULL DEFAULT 0.0"),
    ("avg_batch_latency_s",     "ALTER TABLE runs ADD COLUMN avg_batch_latency_s     REAL    NOT NULL DEFAULT 0.0"),
    ("jobs_per_second",         "ALTER TABLE runs ADD COLUMN jobs_per_second         REAL    NOT NULL DEFAULT 0.0"),
]


class Database:
    """
    SQLite persistence layer for Job objects.

    All reads and writes go through this class. The database file is created
    automatically on first run at the path specified in config.yaml.

    Key design decisions:
    - URL is a UNIQUE constraint — inserting a duplicate URL is a no-op (INSERT OR IGNORE)
    - salary and scores are stored as JSON blobs — avoids extra tables
    - Timestamps are stored as ISO 8601 strings for portability
    """

    def __init__(self, db_path: str) -> None:
        """
        Opens (or creates) the SQLite database at the given path.
        Creates the jobs table if it does not exist.

        Args:
            db_path: Path to the .db file, e.g. 'data/jobs.db'
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row   # rows behave like dicts
        self._conn.execute("PRAGMA journal_mode=WAL")  # safe for concurrent reads

        self._create_tables()
        logger.debug("Database opened: %s", self.db_path)

    def _create_tables(self) -> None:
        """Creates the jobs and runs tables if they do not already exist, then runs migrations."""
        self._conn.execute(CREATE_TABLE_SQL)
        self._conn.execute(CREATE_RUNS_TABLE_SQL)
        self._conn.commit()
        self._run_migrations()

    def _run_migrations(self) -> None:
        """
        Adds any new columns to existing tables.
        Safe to run on every startup — skips columns that already exist.
        """
        jobs_cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        for col_name, alter_sql in _MIGRATIONS:
            if col_name not in jobs_cols:
                self._conn.execute(alter_sql)
                logger.debug("Migration applied (jobs): added column %s", col_name)

        runs_cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        for col_name, alter_sql in _RUNS_MIGRATIONS:
            if col_name not in runs_cols:
                self._conn.execute(alter_sql)
                logger.debug("Migration applied (runs): added column %s", col_name)

        self._conn.commit()

    # ─── Write operations ─────────────────────────────────────────────────────

    def insert_job(self, job: Job) -> Job:
        """
        Inserts a new job into the database.
        If a job with the same URL already exists, the insert is silently ignored.

        Args:
            job: The Job object to insert. id will be set from the database.

        Returns:
            The same Job object with id populated (or the original id if it already existed).
        """
        cursor = self._conn.execute(
            """
            INSERT OR IGNORE INTO jobs
                (url, source, title, company, location, state, work_mode, description,
                 salary_json, scores_json, status, posted_at, expires_at, found_at, applied_at,
                 score_ic, score_architect, score_management, score_best)
            VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            self._to_row(job),
        )
        self._conn.commit()

        if cursor.lastrowid:
            job.id = cursor.lastrowid
            logger.debug("Inserted job id=%d: %s at %s", job.id, job.title, job.company)
        else:
            logger.debug("Job already exists (URL duplicate): %s", job.url)

        return job

    def update_job(self, job: Job) -> None:
        """
        Updates all fields of an existing job record.
        Used after scoring (to save TrackScores) and after applying (to save applied_at).

        Args:
            job: The Job object to update. Must have a non-None id.

        Raises:
            ValueError: If job.id is None.
        """
        if job.id is None:
            raise ValueError("Cannot update a Job that has no id — insert it first.")

        ic, arch, mgmt, best = self._score_values(job)
        self._conn.execute(
            """
            UPDATE jobs SET
                status           = ?,
                scores_json      = ?,
                salary_json      = ?,
                applied_at       = ?,
                location         = ?,
                state            = ?,
                work_mode        = ?,
                description      = ?,
                posted_at        = ?,
                expires_at       = ?,
                score_ic         = ?,
                score_architect  = ?,
                score_management = ?,
                score_best       = ?
            WHERE id = ?
            """,
            (
                job.status,
                job.scores.model_dump_json() if job.scores else None,
                job.salary.model_dump_json() if job.salary else None,
                job.applied_at.isoformat() if job.applied_at else None,
                job.location,
                job.state,
                job.work_mode,
                job.description,
                job.posted_at.isoformat() if job.posted_at else None,
                job.expires_at.isoformat() if job.expires_at else None,
                ic, arch, mgmt, best,
                job.id,
            ),
        )
        self._conn.commit()
        logger.debug("Updated job id=%d", job.id)

    def upsert_job(self, job: Job) -> Job:
        """
        Inserts the job if new, or updates it if the URL already exists.
        Convenience method that combines insert and update.

        Args:
            job: The Job object to upsert.

        Returns:
            The Job with id set.
        """
        existing = self.get_by_url(job.url)
        if existing:
            job.id = existing.id
            self.update_job(job)
        else:
            self.insert_job(job)
        return job

    # ─── Read operations ──────────────────────────────────────────────────────

    def get_by_title_company(self, title: str, company: str) -> Optional[Job]:
        """
        Fetches the first job matching title + company (case-insensitive).
        Used to deduplicate jobs that the same employer posts with different URLs.

        Args:
            title   : Exact job title string.
            company : Exact company name string.

        Returns:
            A Job object if a match exists, None otherwise.
        """
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE lower(title) = lower(?) AND lower(company) = lower(?)",
            (title, company),
        ).fetchone()
        return self._from_row(row) if row else None

    def get_by_url(self, url: str) -> Optional[Job]:
        """
        Fetches a job by its URL.

        Args:
            url: The canonical URL of the job posting.

        Returns:
            A Job object if found, None otherwise.
        """
        row = self._conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
        return self._from_row(row) if row else None

    def get_by_id(self, job_id: int) -> Optional[Job]:
        """
        Fetches a job by its database ID.

        Args:
            job_id: The integer primary key.

        Returns:
            A Job object if found, None otherwise.
        """
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._from_row(row) if row else None

    def get_by_status(self, status: ApplicationStatus) -> list[Job]:
        """
        Returns all jobs with the given application status.

        Args:
            status: The ApplicationStatus to filter by.

        Returns:
            List of Job objects, ordered by found_at descending (newest first).
        """
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY found_at DESC",
            (status.value,),
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def get_all(self) -> list[Job]:
        """
        Returns all jobs in the database, newest first.
        """
        rows = self._conn.execute(
            "SELECT * FROM jobs ORDER BY found_at DESC"
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def count(self) -> int:
        """Returns the total number of jobs in the database."""
        return self._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    # ─── Serialisation helpers ────────────────────────────────────────────────

    @staticmethod
    def _score_values(job: Job) -> tuple[int | None, int | None, int | None, int | None]:
        """
        Extracts (score_ic, score_architect, score_management, score_best) from a job.
        Returns None for any track that was not scored.
        """
        ic   = job.scores.ic.score          if job.scores and job.scores.ic          else None
        arch = job.scores.architect.score   if job.scores and job.scores.architect   else None
        mgmt = job.scores.management.score  if job.scores and job.scores.management  else None
        scores = [s for s in (ic, arch, mgmt) if s is not None]
        best = max(scores) if scores else None
        return ic, arch, mgmt, best

    def _to_row(self, job: Job) -> tuple:
        """
        Converts a Job object to a tuple of values matching the INSERT column order.
        """
        ic, arch, mgmt, best = self._score_values(job)
        return (
            job.url,
            job.source.value if hasattr(job.source, "value") else job.source,
            job.title,
            job.company,
            job.location,
            job.state,
            job.work_mode.value if job.work_mode and hasattr(job.work_mode, "value") else job.work_mode,
            job.description,
            job.salary.model_dump_json() if job.salary else None,
            job.scores.model_dump_json() if job.scores else None,
            job.status.value if hasattr(job.status, "value") else job.status,
            job.posted_at.isoformat() if job.posted_at else None,
            job.expires_at.isoformat() if job.expires_at else None,
            job.found_at.isoformat(),
            job.applied_at.isoformat() if job.applied_at else None,
            ic, arch, mgmt, best,
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Job:
        """
        Converts a sqlite3.Row back into a Job object.
        Deserialises JSON blobs for salary and scores.
        """
        scores = None
        if row["scores_json"]:
            scores = TrackScores.model_validate_json(row["scores_json"])

        salary = None
        if row["salary_json"]:
            salary = SalaryRange.model_validate_json(row["salary_json"])

        return Job(
            id=row["id"],
            url=row["url"],
            source=JobSource(row["source"]),
            title=row["title"],
            company=row["company"],
            location=row["location"],
            state=row["state"] if "state" in row.keys() else None,
            work_mode=WorkMode(row["work_mode"]) if row["work_mode"] else None,
            description=row["description"],
            salary=salary,
            scores=scores or TrackScores(),
            status=ApplicationStatus(row["status"]),
            posted_at=datetime.fromisoformat(row["posted_at"]) if row["posted_at"] else None,
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
            found_at=datetime.fromisoformat(row["found_at"]),
            applied_at=datetime.fromisoformat(row["applied_at"]) if row["applied_at"] else None,
        )

    # ─── Run history ──────────────────────────────────────────────────────────

    def insert_run(
        self,
        jobs_scraped: int,
        jobs_new: int,
        jobs_scored: int,
        jobs_skipped: int,
        batches: int,
        est_cost_usd: float,
        run_at: datetime | None = None,
        tokens_input_scoring: int = 0,
        tokens_output_scoring: int = 0,
        tokens_input_parsing: int = 0,
        tokens_output_parsing: int = 0,
        tokens_input_tailoring: int = 0,
        tokens_output_tailoring: int = 0,
        actual_cost_usd: float = 0.0,
        elapsed_scrape_s: float = 0.0,
        elapsed_score_s: float = 0.0,
        elapsed_total_s: float = 0.0,
        avg_batch_latency_s: float = 0.0,
        jobs_per_second: float = 0.0,
    ) -> int:
        """
        Records one agent execution in the runs table.

        Args:
            jobs_scraped            : Total jobs returned by all scrapers.
            jobs_new                : Jobs newly inserted (de-duplicated).
            jobs_scored             : Jobs successfully scored by Claude.
            jobs_skipped            : Jobs skipped (stale, no description, excluded).
            batches                 : Number of Claude API batch calls made.
            est_cost_usd            : Estimated USD cost (used when no actual tokens available).
            run_at                  : When the run started (captured before scraping).
                                      Defaults to now() if not provided.
            tokens_input_scoring    : Actual input tokens used for job_scoring calls.
            tokens_output_scoring   : Actual output tokens used for job_scoring calls.
            tokens_input_parsing    : Actual input tokens used for resume_parsing calls.
            tokens_output_parsing   : Actual output tokens used for resume_parsing calls.
            tokens_input_tailoring  : Actual input tokens used for resume_tailoring calls.
            tokens_output_tailoring : Actual output tokens used for resume_tailoring calls.
            actual_cost_usd         : Cost derived from real token counts (0.0 if none made).

        Returns:
            The new run id.
        """
        cursor = self._conn.execute(
            """
            INSERT INTO runs (
                run_at, jobs_scraped, jobs_new, jobs_scored, jobs_skipped, batches, est_cost_usd,
                tokens_input_scoring, tokens_output_scoring,
                tokens_input_parsing, tokens_output_parsing,
                tokens_input_tailoring, tokens_output_tailoring,
                actual_cost_usd,
                elapsed_scrape_s, elapsed_score_s, elapsed_total_s,
                avg_batch_latency_s, jobs_per_second
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (run_at or datetime.now(tz=timezone.utc)).isoformat(),
                jobs_scraped, jobs_new, jobs_scored, jobs_skipped, batches,
                round(est_cost_usd, 6),
                tokens_input_scoring, tokens_output_scoring,
                tokens_input_parsing, tokens_output_parsing,
                tokens_input_tailoring, tokens_output_tailoring,
                round(actual_cost_usd, 6),
                round(elapsed_scrape_s, 3), round(elapsed_score_s, 3), round(elapsed_total_s, 3),
                round(avg_batch_latency_s, 3), round(jobs_per_second, 3),
            ),
        )
        self._conn.commit()
        logger.debug(
            "Run recorded: id=%d scored=%d est_cost=$%.4f actual_cost=$%.4f",
            cursor.lastrowid, jobs_scored, est_cost_usd, actual_cost_usd,
        )
        return cursor.lastrowid

    def get_runs(self) -> list[dict]:
        """
        Returns all run records ordered by most recent first.
        Each record is a plain dict matching the runs table columns.
        """
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY run_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def exclude_jobs(self, job_ids: list[int], reason: str) -> None:
        """
        Marks one or more jobs as excluded so they are hidden from all dashboard views.

        Args:
            job_ids: List of job IDs to exclude.
            reason:  Human-readable label stored for reference (e.g. "Not a good fit").
        """
        self._conn.executemany(
            "UPDATE jobs SET excluded = 1, excluded_reason = ? WHERE id = ?",
            [(reason, jid) for jid in job_ids],
        )
        self._conn.commit()
        logger.debug("Excluded %d job(s): reason=%r", len(job_ids), reason)

    def backfill_states(self) -> int:
        """
        Populates the state column for existing rows where state IS NULL but location IS NOT NULL.
        Safe to call on every startup — skips rows that already have a state or have no location.

        Returns:
            Number of rows updated.
        """
        from models.filters import extract_us_state

        rows = self._conn.execute(
            "SELECT id, location FROM jobs WHERE state IS NULL AND location IS NOT NULL"
        ).fetchall()

        updates = [
            (extract_us_state(row[1]), row[0])
            for row in rows
            if extract_us_state(row[1]) is not None
        ]

        if updates:
            self._conn.executemany("UPDATE jobs SET state = ? WHERE id = ?", updates)
            self._conn.commit()
            logger.info("Backfilled state for %d job(s)", len(updates))

        return len(updates)

    def delete_below_threshold(self, threshold: int, dry_run: bool = False) -> int:
        """
        Hard-deletes scored jobs whose best score across all tracks is below threshold.
        Jobs with status 'applied' or 'offer' are never deleted regardless of score.
        Unscored jobs (score_best IS NULL / status NEW) are also left untouched.

        Args:
            threshold: Minimum score to keep (exclusive lower bound). Jobs with
                       score_best < threshold are deleted.
            dry_run:   If True, returns the count without deleting anything.

        Returns:
            Number of rows deleted (or that would be deleted if dry_run=True).
        """
        count = self._conn.execute(
            """
            SELECT COUNT(*) FROM jobs
            WHERE score_best IS NOT NULL
              AND score_best < ?
              AND status NOT IN ('applied', 'offer')
            """,
            (threshold,),
        ).fetchone()[0]

        if not dry_run:
            self._conn.execute(
                """
                DELETE FROM jobs
                WHERE score_best IS NOT NULL
                  AND score_best < ?
                  AND status NOT IN ('applied', 'offer')
                """,
                (threshold,),
            )
            self._conn.commit()
            logger.info("Deleted %d job(s) with score_best < %d", count, threshold)

        return count

    def close(self) -> None:
        """Closes the database connection. Call this on clean shutdown."""
        self._conn.close()
        logger.debug("Database connection closed")
